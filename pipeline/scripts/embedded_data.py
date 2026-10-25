"""Read or replace one line-start JavaScript const containing a JSON value.

Decode JSON to find its actual boundary. Punctuation such as ]; or }; inside a
source string must not be mistaken for the end of a declaration.
"""
import json
import re


def _value_span(text, name, expected_type, required=True):
    declarations = list(re.finditer(r'^[ \t]*const[ \t]+' + re.escape(name) + r'[ \t]*=\s*', text, re.M))
    if not declarations and not required:
        return None
    if len(declarations) != 1:
        raise ValueError('Missing or duplicated JSON declaration: ' + name)
    start = declarations[0].end()
    value, end = json.JSONDecoder().raw_decode(text, start)
    if not text[end:].lstrip().startswith(';'):
        raise ValueError('JSON declaration has no terminating semicolon: ' + name)
    if not isinstance(value, expected_type):
        raise ValueError('Unexpected JSON type for ' + name)
    return value, start, end


def load_json(text, name, expected_type=list, required=True):
    result = _value_span(text, name, expected_type, required)
    return None if result is None else result[0]


def replace_json(text, name, encoded, expected_type=list):
    _, start, end = _value_span(text, name, expected_type)
    if not isinstance(json.loads(encoded), expected_type):
        raise ValueError('Unexpected replacement JSON type for ' + name)
    return text[:start] + encoded + text[end:]
