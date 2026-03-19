"""
Simple Goal Manager - bez bazy danych, używa pliku JSON
"""

import json
import os
from datetime import datetime

GOAL_FILE = 'goal_data.json'

DEFAULT_GOAL = {
    'name': 'Cel finansowy',
    'current': 0,
    'target': 100000,
    'progress': 0,
    'updated_at': datetime.now().isoformat()
}


def get_current_goal():
    """Pobiera aktualny goal z pliku. Zwraca None jeśli nie skonfigurowany."""
    if not os.path.exists(GOAL_FILE):
        return None  # Nie twórz domyślnego — klient sam skonfiguruje
    
    try:
        with open(GOAL_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Oblicz progress
        if data['target'] > 0:
            data['progress'] = round((data['current'] / data['target']) * 100, 1)
        else:
            data['progress'] = 0
        
        return data
    except Exception as e:
        print(f"⚠️ Error loading goal: {e}")
        # W przypadku błędu, zwróć DEFAULT_GOAL z obliczonym progress
        goal = DEFAULT_GOAL.copy()
        if goal['target'] > 0:
            goal['progress'] = round((goal['current'] / goal['target']) * 100, 1)
        else:
            goal['progress'] = 0
        return goal


def save_goal(current, target, name='Hyundai i30 N'):
    """Zapisuje goal do pliku"""
    data = {
        'name': name,
        'current': float(current),
        'target': float(target),
        'updated_at': datetime.now().isoformat()
    }
    
    try:
        with open(GOAL_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✅ Goal saved: {current}/{target} PLN")
        return True
    except Exception as e:
        print(f"❌ Error saving goal: {e}")
        return False


def add_to_goal(amount):
    """Dodaje kwotę do goala"""
    goal = get_current_goal()
    new_current = goal['current'] + amount
    return save_goal(new_current, goal['target'], goal['name'])


def subtract_from_goal(amount):
    """Odejmuje kwotę od goala"""
    goal = get_current_goal()
    new_current = max(0, goal['current'] - amount)
    return save_goal(new_current, goal['target'], goal['name'])


def reset_goal():
    """Resetuje goal do 0"""
    goal = get_current_goal()
    return save_goal(0, goal['target'], goal['name'])


def get_goal_stats():
    """Zwraca statystyki goala"""
    goal = get_current_goal()
    
    remaining = goal['target'] - goal['current']
    progress = round((goal['current'] / goal['target']) * 100, 1) if goal['target'] > 0 else 0
    
    return {
        'name': goal['name'],
        'current': goal['current'],
        'target': goal['target'],
        'remaining': remaining,
        'progress': progress,
        'updated_at': goal.get('updated_at', 'Unknown')
    }
