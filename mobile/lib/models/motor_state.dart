enum MotorDirection { cw, ccw }

extension MotorDirectionX on MotorDirection {
  String get label => this == MotorDirection.cw ? 'CW' : 'CCW';
  MotorDirection get flipped =>
      this == MotorDirection.cw ? MotorDirection.ccw : MotorDirection.cw;
}

class MotorState {
  final bool connected;
  final bool running;
  final int speed;
  final MotorDirection direction;

  const MotorState({
    required this.connected,
    required this.running,
    required this.speed,
    required this.direction,
  });

  static const int minSpeed = 1;
  static const int maxSpeed = 10;

  const MotorState.initial()
      : connected = false,
        running = false,
        speed = 5,
        direction = MotorDirection.cw;

  MotorState copyWith({
    bool? connected,
    bool? running,
    int? speed,
    MotorDirection? direction,
  }) {
    return MotorState(
      connected: connected ?? this.connected,
      running: running ?? this.running,
      speed: speed ?? this.speed,
      direction: direction ?? this.direction,
    );
  }
}
