import 'package:flutter/foundation.dart';

import '../models/motor_state.dart';

/// Abstract interface sterownika silnika fotobudki 360.
///
/// Implementacje: [MockMotorController] (Session 1), a w przyszłości
/// prawdziwy driver Bluetooth bazujący na wynikach reverse engineeringu ChackTok.
abstract class MotorController extends ChangeNotifier {
  MotorState get state;

  bool get isConnected => state.connected;
  bool get isRunning => state.running;
  int get currentSpeed => state.speed;
  MotorDirection get direction => state.direction;

  List<String> get recentLog;

  Future<bool> connect();
  Future<void> disconnect();

  Future<void> start();
  Future<void> stop();

  Future<void> setSpeed(int level);
  Future<void> speedUp();
  Future<void> speedDown();

  Future<void> reverseDirection();
}
