import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'domain/repositories/connection_repository.dart';
import 'domain/repositories/credentials_repository.dart';
import 'domain/repositories/session_cache_repository.dart';
import 'domain/repositories/scan_engine_repository.dart';
import 'domain/repositories/model_path_repository.dart';
import 'domain/repositories/log_repository.dart';
import 'domain/repositories/offline_queue_repository.dart';
import 'features/connection/controller/connection_controller.dart';
import 'features/scanning/controller/scan_controller.dart';
import 'features/grading/controller/submission_controller.dart';
import 'data/scanning/native_cv_bindings.dart';
import 'data/scanning/cv_engine_service.dart';
import 'data/scanning/model_path_service.dart' show PipelinePathService;
import 'data/connection/websocket_client.dart';
import 'data/queue/offline_queue_manager.dart';
import 'data/queue/offline_sync_worker.dart';
import 'data/auth/credentials_cache.dart';
import 'data/session/session_cache_manager.dart';
import 'features/connection/presentation/connection_screen.dart';
import 'core/presentation/native_library_error_screen.dart';
import 'core/logging/debug_log.dart';
import 'features/grading/presentation/duplicate_resolution_dialog.dart';
import 'domain/entities/exam_models.dart' show DuplicateAction, DuplicateComparison;

/// How long the app tolerates being backgrounded (paused, not resumed)
/// before it intentionally disconnects instead of holding a half-dead
/// "connected" slot on the server. Tune here without touching the logic
/// below. See connection_fix_plan.md Phase 3 for the rationale.
const Duration kBackgroundGracePeriod = Duration(seconds: 90);

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

  /// Backs OfflineSyncWorker's DuplicateResolver — shows the exact same
  /// dialog the online submission flow uses (DuplicateResolutionDialog),
  /// but from a background context with no widget of its own to anchor
  /// to, hence going through _navigatorKey the same way LifecycleManager
  /// already does for its own post-disconnect snackbar.
  ///
  /// Throws if there's genuinely nowhere to show it (app fully
  /// backgrounded, no route mounted) — OfflineSyncWorker treats that
  /// exactly like "no resolver available" and leaves the item safely
  /// queued rather than guessing at an answer.
  Future<DuplicateAction> _resolveDuplicateFromDialog(DuplicateComparison comparison) async {
    final context = _navigatorKey.currentContext;
    if (context == null) {
      throw StateError('No navigator context available to show the duplicate dialog.');
    }
    final action = await showDialog<DuplicateAction>(
      context: context,
      barrierDismissible: false,
      builder: (_) => DuplicateResolutionDialog(comparison: comparison),
    );
    if (action == null) {
      // Dialog dismissed some other way than tapping a button (e.g. a
      // system back gesture snuck past barrierDismissible: false on some
      // platform) — treat as "couldn't get a decision," not as any one
      // specific choice.
      throw StateError('Duplicate dialog closed without a decision.');
    }
    return action;
  }

  @override
  Widget build(BuildContext context) {
    if (widget.nativeError != null) {
      return MaterialApp(
        title: 'Perfection Smart Grader',
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
        Provider<ModelPathRepository>(create: (_) => PipelinePathService()),
        Provider<ScanEngineRepository>(
          create: (context) => CvEngineService(context.read<LogRepository>()),
        ),
        // Phase 5: local offline scan queue — sqflite-backed, see
        // offline_queue_manager.dart. dispose() closes the DB + stream.
        Provider<OfflineQueueRepository>(
          create: (_) => OfflineQueueManager(),
          dispose: (_, repo) => repo.dispose(),
        ),
        // Phase 5.6: watches for reconnects and drains the offline queue.
        // `lazy: false` so it starts listening immediately at app launch
        // rather than waiting for something to read it — nothing in the
        // widget tree ever needs to read this provider directly, it just
        // needs to exist and be running.
        Provider<OfflineSyncWorker>(
          lazy: false,
          create: (context) => OfflineSyncWorker(
            context.read<ConnectionRepository>(),
            context.read<OfflineQueueRepository>(),
            context.read<SessionCacheRepository>(),
            log: context.read<LogRepository>(),
            resolveDuplicate: _resolveDuplicateFromDialog,
          )..start(),
          dispose: (_, worker) => worker.dispose(),
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
            context.read<OfflineQueueRepository>(),
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

  // Phase 3: intentional background grace-period. Only ever started from
  // the paused transition (see didChangeAppLifecycleState below), and only
  // when a session is actually live — no point timing out a disconnect
  // that's already disconnected.
  Timer? _backgroundGraceTimer;

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
    _backgroundGraceTimer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    context.read<LogRepository>().log('LifecycleObserver', 'Transitioned to $state');

    if (state == AppLifecycleState.paused) {
      _startBackgroundGraceTimerIfNeeded();
      return;
    }

    if (state == AppLifecycleState.resumed) {
      // Cancel any pending grace-period timeout — we're back before it fired.
      _backgroundGraceTimer?.cancel();
      _backgroundGraceTimer = null;

      final connection = Provider.of<ConnectionController>(context, listen: false);
      // Unchanged from before Phase 3: still needed for the case where the
      // OS/OEM killed the socket despite (or before) our grace period.
      if (connection.reconnectIfNecessary()) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Reconnecting...'), duration: Duration(seconds: 2)),
        );
      }
      return;
    }

    // .inactive and any other transient state: deliberately do nothing.
    // Only a real paused→ transition starts the grace timer, so a brief
    // inactive blip (e.g. a system dialog popping up) doesn't trigger it.
  }

  void _startBackgroundGraceTimerIfNeeded() {
    // Already have one running (shouldn't normally happen — paused doesn't
    // fire twice without a resumed in between — but guard anyway).
    if (_backgroundGraceTimer != null) return;

    final connection = Provider.of<ConnectionController>(context, listen: false);
    // Only time out a disconnect if there's actually a live session to lose.
    // Matches the stated requirement: don't disconnect unless it's connected
    // and stays backgrounded too long, or it disconnects on its own anyway.
    if (connection.phase != SessionPhase.scanning) return;

    _backgroundGraceTimer = Timer(kBackgroundGracePeriod, () {
      _backgroundGraceTimer = null;
      context.read<LogRepository>().log(
            'LifecycleObserver',
            'Background grace period elapsed — disconnecting intentionally',
          );
      Provider.of<ConnectionController>(context, listen: false).disconnectForBackground();
    });
  }

  @override
  Widget build(BuildContext context) => widget.child;
}