#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AKCES HUB - GOAL MANAGER
========================
Zarządzanie celami finansowymi (Hyundai i30 N)
"""

import sqlite3
from modules.database import get_db

def get_current_goal():
    """Pobiera aktualny cel finansowy"""
    conn = get_db()
    goal = conn.execute('SELECT * FROM goal WHERE id = 1').fetchone()

    if not goal:
        return {
            'name': 'Hyundai i30 N',
            'target': 150000,
            'current': 0,
            'image': 'static/goal.jpg',
            'progress': 0
        }
    
    progress = (goal['current_amount'] / goal['target_amount'] * 100) if goal['target_amount'] > 0 else 0
    
    return {
        'name': goal['name'],
        'target': goal['target_amount'],
        'current': goal['current_amount'],
        'image': goal['image_path'],
        'progress': min(progress, 100)  # Cap at 100%
    }

def add_to_goal(amount):
    """Dodaje kwotę do celu"""
    conn = get_db()
    conn.execute('''
        UPDATE goal 
        SET current_amount = current_amount + ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
    ''', (amount,))
    conn.commit()
    return True

def update_goal(target_amount=None, current_amount=None, name=None, image_path=None):
    """Aktualizuje cel finansowy"""
    conn = get_db()
    
    if target_amount is not None:
        conn.execute('UPDATE goal SET target_amount = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1', (target_amount,))
    
    if current_amount is not None:
        conn.execute('UPDATE goal SET current_amount = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1', (current_amount,))
    
    if name is not None:
        conn.execute('UPDATE goal SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1', (name,))
    
    if image_path is not None:
        conn.execute('UPDATE goal SET image_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1', (image_path,))
    
    conn.commit()
    return True

def reset_goal():
    """Resetuje postęp celu do 0"""
    conn = get_db()
    conn.execute('UPDATE goal SET current_amount = 0, updated_at = CURRENT_TIMESTAMP WHERE id = 1')
    conn.commit()
    return True
