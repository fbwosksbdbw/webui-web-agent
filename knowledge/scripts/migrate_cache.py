# -*- coding: utf-8 -*-
"""Migrate schema_cache.json into 5 per-module cache files."""
import json
import os

CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', 'config')
CACHE_DIR = os.path.join(CONFIG_DIR, 'cache')
OLD_FILE = os.path.join(CONFIG_DIR, 'schema_cache.json')

def classify_table(table_name):
    if table_name.startswith('tbdxtrust'):
        return '信托'
    elif table_name.startswith('tbdxfund'):
        return '基金'
    elif table_name.startswith('tbfina') or table_name.startswith('tbhisfina'):
        return '理财'
    elif table_name.startswith('tbpension') or table_name.startswith('tbhispension') or table_name.startswith('tblcdxpension'):
        return '个人养老金'
    return '公共库'

def migrate():
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(OLD_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    modules = {'信托': {}, '基金': {}, '理财': {}, '商业养老金': {}, '个人养老金': {}, '公共库': {}}
    for table_name, info in data.items():
        module = classify_table(table_name)
        modules[module][table_name] = info

    for module_name, tables in modules.items():
        out_path = os.path.join(CACHE_DIR, f'{module_name}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(tables, f, ensure_ascii=False, indent=2)
        print(f'{module_name}.json: {len(tables)} tables')

if __name__ == '__main__':
    migrate()
