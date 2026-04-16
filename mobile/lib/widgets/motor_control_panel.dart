import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/motor_state.dart';
import '../services/motor_controller.dart';
import '../theme/app_theme.dart';
import 'big_button.dart';

class MotorControlPanel extends StatelessWidget {
  const MotorControlPanel({super.key});

  @override
  Widget build(BuildContext context) {
    final motor = context.watch<MotorController>();
    final connected = motor.isConnected;
    final running = motor.isRunning;

    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(flex: 3, child: _StatusCard(motor: motor)),
        const SizedBox(width: 20),
        Expanded(
          flex: 4,
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              _StartStopButton(connected: connected, running: running, motor: motor),
              const SizedBox(height: 24),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  BigButton(
                    label: 'SPEED -',
                    icon: Icons.remove_rounded,
                    color: AppTheme.accent,
                    disabled: !connected || motor.currentSpeed <= MotorState.minSpeed,
                    onTap: motor.speedDown,
                    size: 110,
                  ),
                  BigButton(
                    label: 'REVERSE',
                    subtitle: motor.direction.label,
                    icon: motor.direction == MotorDirection.cw
                        ? Icons.rotate_right_rounded
                        : Icons.rotate_left_rounded,
                    color: AppTheme.primary,
                    disabled: !connected,
                    onTap: motor.reverseDirection,
                    size: 110,
                  ),
                  BigButton(
                    label: 'SPEED +',
                    icon: Icons.add_rounded,
                    color: AppTheme.accent,
                    disabled: !connected || motor.currentSpeed >= MotorState.maxSpeed,
                    onTap: motor.speedUp,
                    size: 110,
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _StartStopButton extends StatelessWidget {
  const _StartStopButton({
    required this.connected,
    required this.running,
    required this.motor,
  });

  final bool connected;
  final bool running;
  final MotorController motor;

  @override
  Widget build(BuildContext context) {
    return BigButton(
      label: running ? 'STOP' : 'START',
      icon: running ? Icons.stop_rounded : Icons.play_arrow_rounded,
      color: running ? AppTheme.danger : AppTheme.success,
      disabled: !connected,
      onTap: running ? motor.stop : motor.start,
      size: 200,
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.motor});

  final MotorController motor;

  @override
  Widget build(BuildContext context) {
    final speedFraction =
        (motor.currentSpeed - MotorState.minSpeed) /
            (MotorState.maxSpeed - MotorState.minSpeed);

    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: AppTheme.surface.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          _RunningBadge(running: motor.isRunning, connected: motor.isConnected),
          Column(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              const Text(
                'PRĘDKOŚĆ',
                style: TextStyle(
                  color: AppTheme.muted,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 2,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                motor.currentSpeed.toString(),
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 96,
                  height: 1,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: LinearProgressIndicator(
                  value: speedFraction.clamp(0.0, 1.0),
                  minHeight: 10,
                  backgroundColor: Colors.white.withValues(alpha: 0.08),
                  valueColor: const AlwaysStoppedAnimation<Color>(AppTheme.primary),
                ),
              ),
            ],
          ),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('KIERUNEK',
                      style: TextStyle(
                          color: AppTheme.muted,
                          fontSize: 11,
                          letterSpacing: 2)),
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      Icon(
                        motor.direction == MotorDirection.cw
                            ? Icons.rotate_right_rounded
                            : Icons.rotate_left_rounded,
                        color: Colors.white,
                        size: 28,
                      ),
                      const SizedBox(width: 8),
                      Text(
                        motor.direction.label,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 24,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _RunningBadge extends StatelessWidget {
  const _RunningBadge({required this.running, required this.connected});

  final bool running;
  final bool connected;

  @override
  Widget build(BuildContext context) {
    final Color color;
    final String label;
    if (!connected) {
      color = AppTheme.muted;
      label = 'OFFLINE';
    } else if (running) {
      color = AppTheme.success;
      label = 'RUNNING';
    } else {
      color = AppTheme.danger;
      label = 'STOPPED';
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.18),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 10,
            height: 10,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 8),
          Text(
            label,
            style: TextStyle(
              color: color,
              fontWeight: FontWeight.w700,
              fontSize: 12,
              letterSpacing: 1.5,
            ),
          ),
        ],
      ),
    );
  }
}
