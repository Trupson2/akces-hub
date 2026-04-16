import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'screens/home_screen.dart';
import 'services/mock_motor_controller.dart';
import 'services/motor_controller.dart';
import 'theme/app_theme.dart';

class AkcesBoothApp extends StatelessWidget {
  const AkcesBoothApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<MotorController>(
      create: (_) => MockMotorController(),
      child: MaterialApp(
        title: 'Akces Booth',
        debugShowCheckedModeBanner: false,
        theme: AppTheme.dark(),
        darkTheme: AppTheme.dark(),
        themeMode: ThemeMode.dark,
        home: const HomeScreen(),
      ),
    );
  }
}
