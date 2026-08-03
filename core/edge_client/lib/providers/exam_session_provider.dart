import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/foundation.dart';
import '../models/exam_models.dart';
import '../services/websocket_client.dart';

enum SessionPhase { disconnected, connecting, needsCalibration, scanning, error }

class ExamSessionProvider extends ChangeNotifier {
  final WebSocketClient _client;
  
  SessionPhase _phase = SessionPhase.disconnected;
  String? _errorMessage;
  MasterPacket? _masterPacket;
  String? _selectedVersion;
  bool _hasCalibratedProfile = false;
  ui.Size? _calibrationSize;
  double _fiducialRatio = 1.414;
  GradeResult? _currentScan;
  DuplicateComparison? _pendingDuplicate;
  String _proctorName = "";

  StreamSubscription? _statusSub;
  StreamSubscription? _eventSub;

  ExamSessionProvider(this._client) {
    _statusSub = _client.statusStream.listen(_onConnectionStatusChanged);
    _eventSub = _client.eventStream.listen(_onServerEvent);
  }

  SessionPhase get phase => _phase;
  String? get errorMessage => _errorMessage;
  MasterPacket? get masterPacket => _masterPacket;
  String? get selectedVersion => _selectedVersion;
  bool get hasCalibratedProfile => _hasCalibratedProfile;
  ui.Size? get calibrationSize => _calibrationSize;
  double get fiducialRatio => _fiducialRatio;
  GradeResult? get currentScan => _currentScan;
  DuplicateComparison? get pendingDuplicate => _pendingDuplicate;
  String get proctorName => _proctorName;

  void _onConnectionStatusChanged(ConnectionStatus status) {
    switch (status) {
      case ConnectionStatus.disconnected:
        _phase = SessionPhase.disconnected;
        break;
      case ConnectionStatus.connecting:
      case ConnectionStatus.authenticating:
        _phase = SessionPhase.connecting;
        break;
      case ConnectionStatus.connected:
        // Handled by AuthSuccess
        break;
      case ConnectionStatus.error:
        _phase = SessionPhase.error;
        break;
    }
    notifyListeners();
  }

  void _onServerEvent(ServerEvent event) {
    if (event is AuthSuccess) {
      _masterPacket = event.masterPacket;
      _errorMessage = null;
      if (_masterPacket!.answerVersions.isNotEmpty) {
        _selectedVersion = _masterPacket!.answerVersions.first;
      }

      if (_hasCalibratedProfile) {
        _phase = SessionPhase.scanning;
      } else {
        _phase = SessionPhase.needsCalibration;
      }
    } else if (event is AuthFailure) {
      _errorMessage = event.message;
      _phase = SessionPhase.error;
    } else if (event is ScoreSaved) {
      // currentScan handling is done in UI (or we just notify)
      // The UI listens to eventStream directly or Provider state to flip UI
    } else if (event is ScoreDuplicate) {
      _pendingDuplicate = event.comparison;
    } else if (event is DuplicateResolved) {
      _pendingDuplicate = null;
    } else if (event is ConnectionLost) {
      _errorMessage = event.reason;
      // Phase goes to connecting from statusStream usually
    }
    notifyListeners();
  }

  Future<void> connect({
    required String host,
    required String name,
    required String password,
  }) async {
    _proctorName = name;
    _errorMessage = null;
    await _client.connect(host: host, name: name, password: password);
  }

  void disconnect() {
    _client.disconnect();
  }

  void selectVersion(String version) {
    _selectedVersion = version;
    notifyListeners();
  }

  void markCalibrated(ui.Size size, {double fiducialRatio = 1.414}) {
    _hasCalibratedProfile = true;
    _calibrationSize = size;
    _fiducialRatio = fiducialRatio;
    _phase = SessionPhase.scanning;
    notifyListeners();
  }

  void requestRecalibrate() {
    _phase = SessionPhase.needsCalibration;
    notifyListeners();
  }

  void setCurrentScan(GradeResult result) {
    _currentScan = result;
    notifyListeners();
  }

  void clearCurrentScan() {
    _currentScan = null;
    _pendingDuplicate = null;
    notifyListeners();
  }

  void updateCurrentScan({required String studentId, required double essayTotal, String? groupType}) {
    if (_currentScan != null) {
      _currentScan!.studentId = studentId;
      _currentScan!.essayTotal = essayTotal;
      _currentScan!.groupType = groupType;
      notifyListeners();
    }
  }

  Future<void> commitCurrentScan() async {
    final timestamp = DateTime.now().toLocal().toString().split('.')[0];
    await submitCurrentScan(timestamp);
  }

  Future<void> submitCurrentScan(String timestamp) async {
    if (_currentScan != null && _currentScan!.studentId != null && _currentScan!.studentId!.isNotEmpty) {
      final payload = _currentScan!.toSubmitScorePayload(timestamp);
      
      if (_currentScan!.imagePath != null) {
        try {
          final bytes = await File(_currentScan!.imagePath!).readAsBytes();
          payload['image_base64'] = base64Encode(bytes);
        } catch (e) {
          debugPrint("Failed to encode image: $e");
        }
      }
      
      _client.submitScore(payload);
    }
  }

  Future<void> resolveDuplicate(DuplicateAction action, String timestamp) async {
    if (_pendingDuplicate != null) {
      Map<String, dynamic>? newPayload;
      if (action == DuplicateAction.overwrite && _currentScan != null) {
        newPayload = _currentScan!.toSubmitScorePayload(timestamp);
        if (_currentScan!.imagePath != null) {
          try {
            final bytes = await File(_currentScan!.imagePath!).readAsBytes();
            newPayload['image_base64'] = base64Encode(bytes);
          } catch (e) {}
        }
      }
      _client.resolveDuplicate(
        studentId: _pendingDuplicate!.studentId,
        action: action,
        newScorePayload: newPayload,
      );
      if (action != DuplicateAction.overwrite) {
        // If not overwrite, we just discard/keep previous and clear the current scan
        clearCurrentScan();
      }
    }
  }

  @override
  void dispose() {
    _statusSub?.cancel();
    _eventSub?.cancel();
    super.dispose();
  }
}
