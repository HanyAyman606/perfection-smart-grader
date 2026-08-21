// Tests for GradingReviewController.idValidationError's format rules:
// one capital letter from the admin-configured pool + exactly
// MasterPacket.idDigitCols (3) digits, zero-padded. See
// connection_fix_plan.md's "Student ID format validation" note.
import 'package:flutter_test/flutter_test.dart';
import 'package:smart_grader/domain/entities/exam_models.dart';
import 'package:smart_grader/data/connection/websocket_client.dart' show AuthSuccess;
import 'package:smart_grader/features/connection/controller/connection_controller.dart';
import 'package:smart_grader/features/scanning/controller/scan_controller.dart';
import 'package:smart_grader/features/grading/controller/grading_review_controller.dart';
import 'package:smart_grader/features/grading/controller/submission_controller.dart';

import 'fakes/fake_connection_repository.dart';
import 'fakes/fake_offline_queue_repository.dart';
import 'fakes/fake_support_repositories.dart';

MasterPacket _masterPacket({List<String> letters = const ['M', 'C', 'D']}) {
  return MasterPacket.fromJson({
    'exam_name': 'Test Exam',
    'exam_mode': 'quiz',
    'id': {'num_digits': 3, 'num_letters': letters.length, 'letters': letters},
  });
}

void main() {
  late FakeConnectionRepository fakeConnection;
  late FakeSessionCacheRepository fakeSessionCache;
  late FakeCredentialsRepository fakeCredentials;
  late ScanController scanController;
  late ConnectionController connectionController;
  late SubmissionController submissionController;
  late GradingReviewController controller;

  Future<void> authWithLetters(List<String> letters) async {
    fakeConnection.emitEvent(AuthSuccess(_masterPacket(letters: letters)));
    // The fake's event stream is a broadcast StreamController, which
    // dispatches to listeners on the next microtask, not synchronously —
    // without this, ConnectionController._onServerEvent hasn't run yet
    // and masterPacket is still null the instant after this call
    // returns, silently skipping the letter-pool check below.
    await Future.delayed(Duration.zero);
  }

  setUp(() {
    fakeConnection = FakeConnectionRepository();
    fakeSessionCache = FakeSessionCacheRepository();
    fakeCredentials = FakeCredentialsRepository();
    scanController = ScanController(fakeSessionCache, fakeConnection);
    connectionController = ConnectionController(fakeConnection, fakeCredentials, fakeSessionCache);
    submissionController = SubmissionController(
      fakeConnection,
      fakeSessionCache,
      scanController,
      FakeOfflineQueueRepository(),
    );
  });

  GradingReviewController buildController() {
    return GradingReviewController(
      scanController: scanController,
      submissionController: submissionController,
      connectionController: connectionController,
      manualEntry: true,
    );
  }

  test('rejects an ID with too few digits ("M69" — missing the zero-pad)', () async {
    await authWithLetters(['M', 'C', 'D']);
    controller = buildController();
    controller.idController.text = 'M69';

    expect(controller.idValidationError, isNotNull);
    expect(controller.idValidationError, contains('M69'));
  });

  test('accepts the correctly zero-padded form ("M069") of the same ID', () async {
    await authWithLetters(['M', 'C', 'D']);
    controller = buildController();
    controller.idController.text = 'M069';

    expect(controller.idValidationError, isNull);
  });

  test('rejects a letter outside the admin-configured pool for this exam', () async {
    await authWithLetters(['M', 'C', 'D']); // no 'Z'
    controller = buildController();
    controller.idController.text = 'Z069';

    final error = controller.idValidationError;
    expect(error, isNotNull);
    expect(error, contains('Z'));
  });

  test('accepts lowercase input, normalizing it the same as the printed '
      'uppercase form', () async {
    await authWithLetters(['M', 'C', 'D']);
    controller = buildController();
    controller.idController.text = 'm069';

    expect(controller.idValidationError, isNull);
  });

  test('rejects too many digits ("M0069")', () async {
    await authWithLetters(['M', 'C', 'D']);
    controller = buildController();
    controller.idController.text = 'M0069';

    expect(controller.idValidationError, isNotNull);
  });

  test('still rejects the all-zero placeholder ID ("M000") even though '
      'it matches the letter+3-digit shape', () async {
    await authWithLetters(['M', 'C', 'D']);
    controller = buildController();
    controller.idController.text = 'M000';

    expect(controller.idValidationError, isNotNull);
  });

  test('when no master packet / letter pool is available yet, still '
      'enforces the letter+3-digit shape but skips the pool check', () {
    // No authWithLetters() call — masterPacket stays null.
    controller = buildController();
    controller.idController.text = 'Q069'; // any letter, since no pool to check against

    expect(controller.idValidationError, isNull);

    controller.idController.text = 'Q69'; // still enforces digit count
    expect(controller.idValidationError, isNotNull);
  });

  test('applying the fields (generateReceipt) normalizes a lowercase-typed '
      'ID to uppercase before it becomes the scan\'s studentId', () async {
    await authWithLetters(['M', 'C', 'D']);
    controller = buildController();
    controller.idController.text = 'm069';

    controller.generateReceipt();

    expect(scanController.currentScan?.studentId, 'M069');
  });
}