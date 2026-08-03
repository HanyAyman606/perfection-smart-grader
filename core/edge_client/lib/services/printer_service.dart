import 'dart:async';
import 'package:flutter/foundation.dart';
import '../models/exam_models.dart';

abstract class ThermalPrinter {
  Future<void> connect();
  Future<void> printReceipt(List<int> bytes);
  Future<void> disconnect();
  bool get isConnected;
}

class DummyThermalPrinter implements ThermalPrinter {
  bool _connected = false;

  @override
  Future<void> connect() async {
    await Future.delayed(const Duration(milliseconds: 400));
    _connected = true;
  }

  @override
  Future<void> printReceipt(List<int> bytes) async {
    await Future.delayed(const Duration(milliseconds: 600));
    if (kDebugMode) {
      print("--- DUMMY PRINTER OUTPUT ---");
      print(String.fromCharCodes(bytes));
      print("----------------------------");
    }
  }

  @override
  Future<void> disconnect() async {
    _connected = false;
  }

  @override
  bool get isConnected => _connected;
}

class BluetoothThermalPrinter implements ThermalPrinter {
  @override
  Future<void> connect() async { throw UnimplementedError(); }
  @override
  Future<void> printReceipt(List<int> bytes) async { throw UnimplementedError(); }
  @override
  Future<void> disconnect() async { throw UnimplementedError(); }
  @override
  bool get isConnected => false;
}

class UsbThermalPrinter implements ThermalPrinter {
  @override
  Future<void> connect() async { throw UnimplementedError(); }
  @override
  Future<void> printReceipt(List<int> bytes) async { throw UnimplementedError(); }
  @override
  Future<void> disconnect() async { throw UnimplementedError(); }
  @override
  bool get isConnected => false;
}

class NetworkThermalPrinter implements ThermalPrinter {
  @override
  Future<void> connect() async { throw UnimplementedError(); }
  @override
  Future<void> printReceipt(List<int> bytes) async { throw UnimplementedError(); }
  @override
  Future<void> disconnect() async { throw UnimplementedError(); }
  @override
  bool get isConnected => false;
}

class PrinterService {
  static final PrinterService instance = PrinterService._();
  PrinterService._();

  ThermalPrinter _printer = DummyThermalPrinter();

  void configure(ThermalPrinter printer) {
    _printer = printer;
  }

  Future<void> printReceipt({
    required String examName,
    required String studentId,
    String? groupType,
    String? answerVersion,
    required double totalScore,
    required double mcqScore,
    required double essayScore,
    required List<Mistake> mistakes,
    required String timestamp,
  }) async {
    if (!_printer.isConnected) {
      await _printer.connect();
    }

    final buffer = StringBuffer();
    buffer.writeln(examName.toUpperCase());
    buffer.writeln("----------------------------");
    buffer.writeln("Student ID: $studentId");
    if (groupType != null && groupType.isNotEmpty) buffer.writeln("Group: $groupType");
    if (answerVersion != null && answerVersion.isNotEmpty) buffer.writeln("Booklet: $answerVersion");
    buffer.writeln("FINAL GRADE: $totalScore");
    buffer.writeln("----------------------------");
    
    if (mistakes.isNotEmpty) {
      buffer.writeln("MISTAKES:");
      for (final m in mistakes) {
        buffer.writeln("Q${m.question} - Correct: ${m.correct}   Your answer: ${m.given}");
      }
      buffer.writeln("----------------------------");
    }
    
    buffer.writeln("MCQ Grade: $mcqScore");
    buffer.writeln("Essay Grade: $essayScore");
    buffer.writeln("----------------------------");
    buffer.writeln(timestamp);
    
    final bytes = buffer.toString().codeUnits;
    await _printer.printReceipt(bytes);
  }
}
