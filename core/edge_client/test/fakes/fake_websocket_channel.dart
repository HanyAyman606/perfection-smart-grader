import 'dart:async';
import 'package:stream_channel/stream_channel.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:smart_grader/domain/repositories/log_repository.dart';

/// Stands in for a real WebSocketChannel so WebSocketClient can be tested
/// without opening a socket. Injected via WebSocketClient's
/// `channelFactory` constructor parameter (see websocket_client.dart).
///
/// `with StreamChannelMixin` supplies default implementations of
/// cast/pipe/transform/etc. (required because WebSocketChannel itself mixes
/// this in — `implements` alone doesn't inherit those) so this class only
/// has to provide the WebSocket-specific members below.
class FakeWebSocketChannel with StreamChannelMixin implements WebSocketChannel {
  FakeWebSocketChannel({Future<void>? readyFuture})
      : ready = readyFuture ?? Future.value();

  final StreamController<dynamic> _incoming = StreamController<dynamic>.broadcast();
  final List<String> sentMessages = [];
  bool sinkClosed = false;

  @override
  final Future<void> ready;

  @override
  Stream get stream => _incoming.stream;

  @override
  WebSocketSink get sink => _FakeSink(this);

  @override
  String? get protocol => null;

  @override
  int? get closeCode => null;

  @override
  String? get closeReason => null;

  /// Simulate the fake "server" pushing a message down to the client.
  void emitFromServer(String message) => _incoming.add(message);

  /// Simulate the socket erroring out from underneath the client.
  void emitError(Object error) => _incoming.addError(error);

  /// Simulate a clean/unclean close (fires the client's onDone).
  void emitDone() => _incoming.close();
}

class _FakeSink implements WebSocketSink {
  _FakeSink(this._channel);
  final FakeWebSocketChannel _channel;

  @override
  void add(dynamic data) => _channel.sentMessages.add(data as String);

  @override
  void addError(Object error, [StackTrace? stackTrace]) {}

  @override
  Future addStream(Stream stream) async {}

  @override
  Future close([int? closeCode, String? closeReason]) async {
    _channel.sinkClosed = true;
  }

  @override
  Future get done => Future.value();
}

/// No-op LogRepository — WebSocketClient logs every send/recv, tests don't
/// care about the log content, just need something to satisfy the
/// constructor.
class FakeLogRepository implements LogRepository {
  final List<String> lines = [];

  @override
  void log(String tag, String message) {
    lines.add('$tag: $message');
  }
}