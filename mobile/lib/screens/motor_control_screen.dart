import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/motor_controller.dart';
import '../theme/app_theme.dart';
import '../widgets/motor_control_panel.dart';

class MotorControlScreen extends StatelessWidget {
  const MotorControlScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      extendBodyBehindAppBar: true,
      body: Container(
        decoration: const BoxDecoration(gradient: AppTheme.backgroundGradient),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(24, 16, 24, 16),
            child: Column(
              children: [
                _TopBar(),
                const SizedBox(height: 16),
                const Expanded(child: MotorControlPanel()),
                const SizedBox(height: 12),
                const _DebugLogPanel(),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _TopBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final motor = context.watch<MotorController>();
    final connected = motor.isConnected;

    return Row(
      children: [
        IconButton(
          onPressed: () => Navigator.of(context).maybePop(),
          icon: const Icon(Icons.arrow_back_ios_new_rounded),
          color: Colors.white,
        ),
        const SizedBox(width: 4),
        const Text(
          'Sterowanie silnikiem',
          style: TextStyle(
            color: Colors.white,
            fontSize: 22,
            fontWeight: FontWeight.w700,
          ),
        ),
        const Spacer(),
        _ConnectButton(connected: connected, motor: motor),
      ],
    );
  }
}

class _ConnectButton extends StatefulWidget {
  const _ConnectButton({required this.connected, required this.motor});

  final bool connected;
  final MotorController motor;

  @override
  State<_ConnectButton> createState() => _ConnectButtonState();
}

class _ConnectButtonState extends State<_ConnectButton> {
  bool _busy = false;

  Future<void> _toggle() async {
    setState(() => _busy = true);
    try {
      if (widget.connected) {
        await widget.motor.disconnect();
      } else {
        await widget.motor.connect();
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final Color color = widget.connected ? AppTheme.success : AppTheme.primary;
    return FilledButton.icon(
      onPressed: _busy ? null : _toggle,
      style: FilledButton.styleFrom(
        backgroundColor: color,
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
      icon: _busy
          ? const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
            )
          : Icon(widget.connected
              ? Icons.bluetooth_connected_rounded
              : Icons.bluetooth_searching_rounded),
      label: Text(
        _busy
            ? 'Łączenie...'
            : widget.connected
                ? 'Rozłącz'
                : 'Połącz z fotobudką',
        style: const TextStyle(fontWeight: FontWeight.w700),
      ),
    );
  }
}

class _DebugLogPanel extends StatelessWidget {
  const _DebugLogPanel();

  @override
  Widget build(BuildContext context) {
    final motor = context.watch<MotorController>();
    final log = motor.recentLog;

    return Container(
      height: 120,
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.35),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.white.withValues(alpha: 0.05)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: const [
              Icon(Icons.terminal_rounded, color: AppTheme.muted, size: 16),
              SizedBox(width: 6),
              Text(
                'DEBUG LOG (mock)',
                style: TextStyle(
                  color: AppTheme.muted,
                  fontSize: 11,
                  letterSpacing: 2,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Expanded(
            child: log.isEmpty
                ? const Center(
                    child: Text(
                      'Brak komend. Kliknij przycisk aby zobaczyć mock log.',
                      style: TextStyle(color: AppTheme.muted, fontSize: 12),
                    ),
                  )
                : ListView.builder(
                    padding: EdgeInsets.zero,
                    itemCount: log.length,
                    itemBuilder: (_, i) => Text(
                      log[i],
                      style: const TextStyle(
                        fontFamily: 'monospace',
                        color: Colors.greenAccent,
                        fontSize: 12,
                      ),
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}
