import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

class ImageSize {
  static Future<ui.Size> decodeImageSize(String imagePath) async {
    final bytes = await File(imagePath).readAsBytes();
    final completer = Completer<ui.Size>();
    
    ui.decodeImageFromList(bytes, (ui.Image img) {
      completer.complete(ui.Size(img.width.toDouble(), img.height.toDouble()));
      img.dispose();
    });
    
    return completer.future;
  }
}
