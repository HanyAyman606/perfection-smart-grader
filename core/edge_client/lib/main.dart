<<<<<<< HEAD
// Flutter entry point
=======
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'providers/exam_session_provider.dart';
import 'services/native_omr_bindings.dart';
import 'services/websocket_client.dart';
import 'screens/connection_screen.dart';
import 'screens/native_library_error_screen.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();

  // Probe for native library before starting the app
  final nativeError = NativeOmrBindings.probeAvailability();

  runApp(NexusEdgeApp(nativeError: nativeError));
}

class NexusEdgeApp extends StatelessWidget {
  final String? nativeError;

  const NexusEdgeApp({super.key, this.nativeError});

  @override
  Widget build(BuildContext context) {
    if (nativeError != null) {
      return MaterialApp(
        title: 'Nexus Edge',
        theme: ThemeData.dark(),
        home: NativeLibraryErrorScreen(error: nativeError!),
      );
    }

    return MultiProvider(
      providers: [
        Provider<WebSocketClient>(
          create: (_) => WebSocketClient(),
          dispose: (_, client) => client.dispose(),
        ),
        ChangeNotifierProvider<ExamSessionProvider>(
          create: (context) => ExamSessionProvider(
            Provider.of<WebSocketClient>(context, listen: false),
          ),
        ),
      ],
      child: MaterialApp(
        title: 'Nexus Edge',
        theme: ThemeData(
          brightness: Brightness.dark,
          primarySwatch: Colors.blue,
          useMaterial3: true,
        ),
        home: const ConnectionScreen(),
      ),
    );
  }
}
>>>>>>> af9284c712fd3317b7fecb1c4c6ba726ec81c9a6
