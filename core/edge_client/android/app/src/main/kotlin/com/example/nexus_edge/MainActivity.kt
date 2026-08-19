package com.example.nexus_edge

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val CHANNEL = "com.example.nexus_edge/native_lib_dir"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                if (call.method == "getNativeLibDir") {
                    // The one reliable source of truth for where the OS
                    // actually extracted this app's native libs (including
                    // our repackaged lib<stage>.so binaries) at install
                    // time. Guessing this path from applicationSupportDirectory
                    // (as the old Dart-side heuristic did) is not correct.
                    result.success(applicationInfo.nativeLibraryDir)
                } else {
                    result.notImplemented()
                }
            }
    }
}