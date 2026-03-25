#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Centralized logging for AKCES HUB.
Writes to console AND logs/akces_hub.log with rotation.
"""

import os
import logging
from logging.handlers import RotatingFileHandler

# Auto-create logs directory next to app.py (project root)
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, 'akces_hub.log')

# Create the logger
_logger = logging.getLogger('akces_hub')
_logger.setLevel(logging.DEBUG)

# Prevent duplicate handlers if module is imported multiple times
if not _logger.handlers:
    # Format: [2026-03-14 22:30:00] <span class=material-symbols-outlined>info</span> message
    _formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s',
                                   datefmt='%Y-%m-%d %H:%M:%S')

    # Console handler
    _console = logging.StreamHandler()
    _console.setLevel(logging.INFO)
    _console.setFormatter(_formatter)
    _logger.addHandler(_console)

    # File handler with rotation: 5 MB max, keep 5 backups
    _file = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=5,
                                encoding='utf-8')
    _file.setLevel(logging.DEBUG)
    _file.setFormatter(_formatter)
    _logger.addHandler(_file)


def log(msg):
    """Log an INFO message."""
    _logger.info(msg)


def log_error(msg):
    """Log an ERROR message."""
    _logger.error(msg)


def log_warning(msg):
    """Log a WARNING message."""
    _logger.warning(msg)


def log_debug(msg):
    """Log a DEBUG message (file only, not console)."""
    _logger.debug(msg)
