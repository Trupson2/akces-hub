import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';

import '../models/motor_state.dart';
import 'motor_controller.dart';

/// Mock implementacja [MotorController] - drukuje komendy do konsoli
/// zamiast wysyłać przez Bluetooth. Zamieniamy na prawdziwy driver w Sesji 2.
class MockMotorController extends MotorController {
  MotorState _state = const MotorState.initial();
  final List<String> _log = <String>[];
  final Random _rng = Random();

  static const int _logLimit = 10;

  @override
  MotorState get state => _state;

  @override
  List<String> get recentLog => List.unmodifiable(_log);

  void _logCmd(String msg) {
    final line = '[MOCK ${_ts()}] $msg';
    debugPrint(line);
    _log.insert(0, line);
    if (_log.length > _logLimit) {
      _log.removeRange(_logLimit, _log.length);
    }
  }

  String _ts() {
    final now = DateTime.now();
    String two(int n) => n.toString().padLeft(2, '0');
    String three(int n) => n.toString().padLeft(3, '0');
    return '${two(now.hour)}:${two(now.minute)}:${two(now.second)}.${three(now.millisecond)}';
  }

  Future<void> _fakeBtLatency() {
    final ms = 100 + _rng.nextInt(201);
    return Future<void>.delayed(Duration(milliseconds: ms));
  }

  @override
  Future<bool> connect() async {
    _logCmd('CONNECT - scanning for device...');
    await _fakeBtLatency();
    _logCmd('CONNECT - paired with mock booth (would send: [0xA5, 0xC0, 0x01])');
    _state = _state.copyWith(connected: true);
    notifyListeners();
    return true;
  }

  @override
  Future<void> disconnect() async {
    _logCmd('DISCONNECT cmd - would send: [0xA5, 0xC0, 0x00]');
    await _fakeBtLatency();
    _state = const MotorState.initial();
    notifyListeners();
  }

  @override
  Future<void> start() async {
    if (!_state.connected) {
      _logCmd('START ignored - not connected');
      return;
    }
    _logCmd('START cmd - would send: [0xA5, 0x01, 0x00]');
    _state = _state.copyWith(running: true);
    notifyListeners();
  }

  @override
  Future<void> stop() async {
    if (!_state.connected) {
      _logCmd('STOP ignored - not connected');
      return;
    }
    _logCmd('STOP cmd - would send: [0xA5, 0x02, 0x00]');
    _state = _state.copyWith(running: false);
    notifyListeners();
  }

  @override
  Future<void> setSpeed(int level) async {
    final clamped = level.clamp(MotorState.minSpeed, MotorState.maxSpeed);
    final hex = clamped.toRadixString(16).padLeft(2, '0').toUpperCase();
    _logCmd('SET_SPEED=$clamped cmd - would send: [0xA5, 0x03, 0x$hex]');
    _state = _state.copyWith(speed: clamped);
    notifyListeners();
  }

  @override
  Future<void> speedUp() => setSpeed(_state.speed + 1);

  @override
  Future<void> speedDown() => setSpeed(_state.speed - 1);

  @override
  Future<void> reverseDirection() async {
    final next = _state.direction.flipped;
    final byte = next == MotorDirection.cw ? '0x00' : '0x01';
    _logCmd('REVERSE - new direction ${next.label} (would send: [0xA5, 0x04, $byte])');
    _state = _state.copyWith(direction: next);
    notifyListeners();
  }
}
