import 'dart:io';
import 'package:flutter/services.dart' show rootBundle;
import 'package:path_provider/path_provider.dart';
import '../../domain/repositories/model_path_repository.dart';

/// The ONNX model ships as a Flutter asset (bundled at build time), but
/// the C++ engine reads it via a plain filesystem path (config.h's
/// model_path field), not Flutter's asset bundle. This copies it out to
/// app support storage once and reuses that path afterward.
class ModelPathService implements ModelPathRepository {
  ModelPathService();

  static const String _assetPath = 'assets/models/bubble.onnx';
  String? _cachedPath;

  @override
  Future<String> resolveBubbleModelPath() async {
    if (_cachedPath != null && await File(_cachedPath!).exists()) {
      return _cachedPath!;
    }

    final appDir = await getApplicationSupportDirectory();
    final outPath = '${appDir.path}/bubble.onnx';
    final outFile = File(outPath);

    if (!await outFile.exists()) {
      final data = await rootBundle.load(_assetPath);
      await outFile.writeAsBytes(data.buffer.asUint8List(), flush: true);
    }

    _cachedPath = outPath;
    return outPath;
  }
}
