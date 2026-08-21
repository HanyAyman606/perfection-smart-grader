// Phase 3 tests — see connection_fix_plan.md.
//
// Uses flutter_test's built-in virtual clock (testWidgets already runs the
// test body inside a FakeAsync zone, so real Timer() instances created by
// LifecycleManager are advanced via tester.pump(duration) rather than real
// wall-clock waiting) instead of the `fake_async` package directly — no
// need for it here since WidgetTester already gives us that seam.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:smart_grader/main.dart' show LifecycleManager, kBackgroundGracePeriod;
import 'package:smart_grader/domain/repositories/connection_repository.dart';
import 'package:smart_grader/domain/repositories/credentials_repository.dart';
import 'package:smart_grader/domain/repositories/session_cache_repository.dart';
import 'package:smart_grader/domain/repositories/log_repository.dart';
import 'package:smart_grader/domain/entities/exam_models.dart';
import 'package:smart_grader/data/connection/websocket_client.dart' show AuthSuccess;
import 'package:smart_grader/features/connection/controller/connection_controller.dart';

import 'fakes/fake_connection_repository.dart';
import 'fakes/fake_support_repositories.dart';

MasterPacket _fakeMasterPacket() {
  return MasterPacket(
    examName: 'Test Exam',
    examMode: 'quiz',
    mcqCount: 1,
    mcqRanges: const [],
    hasEssays: false,
    essayPointsMap: const {},
    groupName: 'A',
    answerVersions: const ['A'],
    modelAnswers: const {
      'A': {'1': 'A'}
    },
    voidedQuestions: const {},
    choicesPerQuestion: 4,
    mcqColumns: McqColumnLayout(numCols: 1, columns: {1: 1}),
    idNumDigits: 3,
    idNumLetters: 1,
    idLetters: const ['A'],
  );
}

void main() {
  late FakeConnectionRepository fakeRepo;
  late ConnectionController controller;
  final navigatorKey = GlobalKey<NavigatorState>();

  Widget buildApp() {
    return MultiProvider(
      providers: [
        Provider<LogRepository>(create: (_) => FakeLogRepository()),
        Provider<ConnectionRepository>(create: (_) => fakeRepo),
        ChangeNotifierProvider<ConnectionController>.value(value: controller),
      ],
      child: MaterialApp(
        navigatorKey: navigatorKey,
        home: LifecycleManager(
          navigatorKey: navigatorKey,
          child: const Scaffold(body: SizedBox.shrink()),
        ),
      ),
    );
  }

  /// Drives the controller into SessionPhase.scanning the same way
  /// production code does: connect() then a real AuthSuccess event.
  Future<void> becomeScanning(WidgetTester tester) async {
    await controller.connect(host: 'h', name: 'proctor', password: 'p');
    fakeRepo.emitEvent(AuthSuccess(_fakeMasterPacket()));
    await tester.pump();
    expect(controller.phase, SessionPhase.scanning);
  }

  setUp(() {
    fakeRepo = FakeConnectionRepository();
    controller = ConnectionController(
      fakeRepo,
      FakeCredentialsRepository(),
      FakeSessionCacheRepository(),
    );
  });

  testWidgets('short background does not disconnect', (tester) async {
    await tester.pumpWidget(buildApp());
    await becomeScanning(tester);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump(kBackgroundGracePeriod ~/ 2);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();

    // Let any (cancelled) timer's would-be fire time pass, to prove it
    // really was cancelled and not just "hasn't fired yet".
    await tester.pump(kBackgroundGracePeriod);

    expect(fakeRepo.disconnectCallCount, 0);
  });

  testWidgets('long background triggers intentional disconnect', (tester) async {
    await tester.pumpWidget(buildApp());
    await becomeScanning(tester);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump(kBackgroundGracePeriod + const Duration(seconds: 1));

    expect(fakeRepo.disconnectCallCount, 1);
  });

  testWidgets('already disconnected does not start timer work', (tester) async {
    await tester.pumpWidget(buildApp());
    // Deliberately do NOT call becomeScanning — controller starts at
    // SessionPhase.disconnected.
    expect(controller.phase, SessionPhase.disconnected);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump(kBackgroundGracePeriod + const Duration(seconds: 1));

    expect(fakeRepo.disconnectCallCount, 0);
  });

  testWidgets('resume after grace-period disconnect triggers a real '
      'reconnect (not a drop to login)', (tester) async {
    await tester.pumpWidget(buildApp());
    await becomeScanning(tester);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.pump(kBackgroundGracePeriod + const Duration(seconds: 1));
    expect(fakeRepo.disconnectCallCount, 1);
    // The grace-timer disconnect goes through
    // ConnectionController.disconnectForBackground(), which — unlike a
    // normal disconnect() / the SessionEnded path — deliberately keeps
    // `phase` at `scanning` so reconnectIfNecessary() does a real
    // reconnect on resume instead of requiring a fresh login.
    expect(controller.phase, SessionPhase.scanning);

    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump();

    expect(fakeRepo.reconnectCallCount, 1);
  });
}