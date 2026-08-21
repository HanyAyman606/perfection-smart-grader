// Phase 2.2 of connection_fix_plan.md.
//
// IMPORTANT — read before running: this file (and its FakeWebSocketChannel
// helper in test/fakes/fake_websocket_channel.dart) was written without
// access to a Dart/Flutter toolchain — there was no way to run
// `flutter pub get` / `flutter test` in the environment these were authored
// in. The logic has been reasoned through carefully against the real
// websocket_client.dart source, but it has NOT been compiled or executed.
// Run `flutter test test/websocket_client_test.dart` yourself and treat any
// compile error as an integration issue to fix (most likely culprit: the
// exact abstract member set of WebSocketChannel in whatever
// web_socket_channel version pubspec.lock pins — see the note in
// fake_websocket_channel.dart), not a sign the underlying fix is wrong.
// Update connection_fix_plan.md's status table once this has actually run
// green on real Flutter tooling.

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:smart_grader/data/connection/websocket_client.dart';

import 'fakes/fake_websocket_channel.dart';

/// Every MasterPacket field has a `??` fallback in fromJson, so `{}` alone
/// is a valid, minimal master_packet payload for these tests — they only
/// care about the auth_result envelope, not exam content.
const _minimalMasterPacketJson = '{}';

WebSocketClient _buildClient(List<FakeWebSocketChannel> channels, FakeLogRepository log) {
  return WebSocketClient(
    log,
    channelFactory: (uri) {
      final channel = FakeWebSocketChannel();
      channels.add(channel);
      return channel;
    },
  );
}

void main() {
  group('WebSocketClient auth failure handling (Phase 2.1 fix)', () {
    test('wrong password auth failure is fatal — no auto-retry', () {
      fakeAsync((async) {
        final channels = <FakeWebSocketChannel>[];
        final client = _buildClient(channels, FakeLogRepository());
        final statuses = <ConnectionStatus>[];
        final events = <ServerEvent>[];
        client.statusStream.listen(statuses.add);
        client.eventStream.listen(events.add);

        client.connect(host: '192.168.1.5', name: 'Ali', password: 'wrong');
        async.flushMicrotasks();
        expect(channels.length, 1);

        channels.first.emitFromServer(
          '{"type":"auth_result","status":"error","reason":"bad_credentials",'
          '"message":"Incorrect password."}',
        );
        async.flushMicrotasks();

        expect(client.status, ConnectionStatus.error);
        final failure = events.whereType<AuthFailure>().last;
        expect(failure.isRetryable, isFalse);

        // Advance well past any possible backoff window (max is 30s) and
        // confirm no second connection attempt was ever made.
        async.elapse(const Duration(seconds: 60));
        expect(channels.length, 1,
            reason: 'a fatal auth failure must never trigger auto-reconnect');
      });
    });

    test('name_taken auth failure triggers retry, not a dead-end error', () {
      fakeAsync((async) {
        final channels = <FakeWebSocketChannel>[];
        final client = _buildClient(channels, FakeLogRepository());
        final statuses = <ConnectionStatus>[];
        final events = <ServerEvent>[];
        client.statusStream.listen(statuses.add);
        client.eventStream.listen(events.add);

        client.connect(host: '192.168.1.5', name: 'Ali', password: '12345678');
        async.flushMicrotasks();
        expect(channels.length, 1);

        channels.first.emitFromServer(
          '{"type":"auth_result","status":"error","reason":"name_taken",'
          '"message":"\'Ali\' is already connected from another device."}',
        );
        async.flushMicrotasks();

        final failure = events.whereType<AuthFailure>().last;
        expect(failure.isRetryable, isTrue,
            reason: 'name_taken must be surfaced as retryable so '
                'ConnectionController does not show a stuck error phase');
        expect(client.status, ConnectionStatus.connecting,
            reason: 'a retryable auth failure should behave like a dropped '
                'connection, not a terminal error');

        // First backoff delay is 3 seconds (see _scheduleReconnect).
        async.elapse(const Duration(seconds: 3));
        expect(channels.length, 2,
            reason: 'should have opened a second channel to retry auth '
                'without any manual re-login');
      });
    });

    test('unexpected connection loss still retries (regression guard)', () {
      fakeAsync((async) {
        final channels = <FakeWebSocketChannel>[];
        final client = _buildClient(channels, FakeLogRepository());

        client.connect(host: '192.168.1.5', name: 'Ali', password: '12345678');
        async.flushMicrotasks();

        channels.first.emitFromServer(
          '{"type":"auth_result","status":"success","master_packet":$_minimalMasterPacketJson}',
        );
        async.flushMicrotasks();
        expect(client.status, ConnectionStatus.connected);

        // Socket dies unexpectedly — NOT an auth failure, e.g. the hotspot
        // dropping mid-session.
        channels.first.emitDone();
        async.flushMicrotasks();

        expect(client.status, ConnectionStatus.connecting);
        async.elapse(const Duration(seconds: 3));
        expect(channels.length, 2,
            reason: 'an unexpected drop must still auto-reconnect exactly '
                'as before Phase 2 (this path was not touched by the fix)');
      });
    });

    test('successful auth resets backoff to the initial 3s delay', () {
      fakeAsync((async) {
        final channels = <FakeWebSocketChannel>[];
        final client = _buildClient(channels, FakeLogRepository());

        client.connect(host: '192.168.1.5', name: 'Ali', password: '12345678');
        async.flushMicrotasks();

        // First unexpected drop -> backoff starts at 3s.
        channels[0].emitDone();
        async.flushMicrotasks();
        async.elapse(const Duration(seconds: 3));
        expect(channels.length, 2);

        // Second unexpected drop without ever succeeding in between ->
        // backoff should have doubled to 6s (3 * 2^1).
        channels[1].emitDone();
        async.flushMicrotasks();
        async.elapse(const Duration(seconds: 3));
        expect(channels.length, 2,
            reason: 'backoff should now be 6s, so 3s alone must not be enough');
        async.elapse(const Duration(seconds: 3)); // total 6s elapsed
        expect(channels.length, 3);

        // Now let this attempt succeed.
        channels[2].emitFromServer(
          '{"type":"auth_result","status":"success","master_packet":$_minimalMasterPacketJson}',
        );
        async.flushMicrotasks();
        expect(client.status, ConnectionStatus.connected);

        // Drop again — if backoff correctly reset on success, the next
        // retry should come back at 3s again, not continue escalating
        // (12s, the next step in the un-reset sequence).
        channels[2].emitDone();
        async.flushMicrotasks();
        async.elapse(const Duration(seconds: 3));
        expect(channels.length, 4,
            reason: 'backoff must reset to 3s after a successful auth, '
                'not keep escalating from the pre-success attempt count');
      });
    });
  });
}