import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'domain/repositories/connection_repository.dart';
import 'domain/repositories/credentials_repository.dart';
import 'domain/repositories/session_cache_repository.dart';
import 'domain/repositories/scan_engine_repository.dart';
import 'domain/repositories/model_path_repository.dart';
import 'domain/repositories/log_repository.dart';
import 'features/connection/controller/connection_controller.dart';
import 'features/scanning/controller/scan_controller.dart';
import 'features/grading/controller/submission_controller.dart';
import 'data/scanning/native_cv_bindings.dart';
import 'data/scanning/cv_engine_service.dart';
import 'data/scanning/model_path_service.dart';
import 'data/connection/websocket_client.dart';
import 'data/auth/credentials_cache.dart';
import 'data/session/session_cache_manager.dart';
import 'features/connection/presentation/connection_screen.dart';
import 'core/presentation/native_library_error_screen.dart';
import 'core/logging/debug_log.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();

  final nativeError = NativeCvBindings.probeAvailability();

  runApp(NexusEdgeApp(nativeError: nativeError));
}

class NexusEdgeApp extends StatefulWidget {
  final String? nativeError;

  const NexusEdgeApp({super.key, this.nativeError});

  @override
  State<NexusEdgeApp> createState() => _NexusEdgeAppState();
}

class _NexusEdgeAppState extends State<NexusEdgeApp> {
  final _navigatorKey = GlobalKey<NavigatorState>();

  @override
  Widget build(BuildContext context) {
    if (widget.nativeError != null) {
      return MaterialApp(
        title: 'Nexus Edge',
        theme: ThemeData.dark(),
        home: NativeLibraryErrorScreen(error: widget.nativeError!),
      );
    }

    return MultiProvider(
      providers: [
        // --- cross-cutting: one DebugLog instance, exposed both as the
        // concrete type (DebugLogScreen needs entries/clear/asText) and
        // as the LogRepository interface (everything else only needs
        // to log) ---
        Provider<DebugLog>(create: (_) => DebugLog()),
        Provider<LogRepository>(create: (context) => context.read<DebugLog>()),

        // --- data layer bound to domain interfaces (composition root) ---
        Provider<ConnectionRepository>(
          create: (context) => WebSocketClient(context.read<LogRepository>()),
          dispose: (_, repo) => repo.dispose(),
        ),
        Provider<CredentialsRepository>(create: (_) => CredentialsCache()),
        Provider<SessionCacheRepository>(create: (_) => SessionCacheManager()),
        Provider<ModelPathRepository>(create: (_) => ModelPathService()),
        Provider<ScanEngineRepository>(
          create: (context) => CvEngineService(context.read<LogRepository>()),
        ),

        // --- feature controllers, depending only on the interfaces above ---
        ChangeNotifierProvider<ConnectionController>(
          create: (context) => ConnectionController(
            context.read<ConnectionRepository>(),
            context.read<CredentialsRepository>(),
            context.read<SessionCacheRepository>(),
          ),
        ),
        ChangeNotifierProvider<ScanController>(
          create: (context) => ScanController(
            context.read<SessionCacheRepository>(),
            context.read<ConnectionRepository>(),
          ),
        ),
        Provider<SubmissionController>(
          create: (context) => SubmissionController(
            context.read<ConnectionRepository>(),
            context.read<SessionCacheRepository>(),
            context.read<ScanController>(),
          ),
        ),
      ],
      child: MaterialApp(
        navigatorKey: _navigatorKey,
        title: 'Nexus Edge',
        theme: ThemeData(
          brightness: Brightness.dark,
          primarySwatch: Colors.blue,
          useMaterial3: true,
        ),
        builder: (context, child) => LifecycleManager(
          navigatorKey: _navigatorKey,
          child: child!,
        ),
        home: const ConnectionScreen(),
      ),
    );
  }
}

class LifecycleManager extends StatefulWidget {
  final Widget child;
  final GlobalKey<NavigatorState> navigatorKey;
  const LifecycleManager({super.key, required this.child, required this.navigatorKey});

  @override
  State<LifecycleManager> createState() => _LifecycleManagerState();
}

class _LifecycleManagerState extends State<LifecycleManager> with WidgetsBindingObserver {
  StreamSubscription? _sessionEventSub;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Subscribed once here (not per-screen) so the app returns to the
    // login page no matter where the proctor currently is — mid-scan,
    // reviewing a grade, looking at the receipt, etc. — the moment the
    // admin closes the server on the dashboard.
    _sessionEventSub ??= context.read<ConnectionRepository>().eventStream.listen((event) {
      if (event is SessionEnded) _returnToLogin();
    });
  }

  void _returnToLogin() {
    final navigator = widget.navigatorKey.currentState;
    if (navigator == null) return;
    navigator.pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const ConnectionScreen()),
      (route) => false,
    );
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final ctx = widget.navigatorKey.currentContext;
      if (ctx == null) return;
      ScaffoldMessenger.of(ctx).showSnackBar(
        const SnackBar(content: Text('This grading session was closed by the admin.')),
      );
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _sessionEventSub?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    context.read<LogRepository>().log('LifecycleObserver', 'Transitioned to $state');
    if (state == AppLifecycleState.resumed) {
      final connection = Provider.of<ConnectionController>(context, listen: false);
      if (connection.reconnectIfNecessary()) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Reconnecting...'), duration: Duration(seconds: 2)),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) => widget.child;
}