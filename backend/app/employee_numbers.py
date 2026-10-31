"""Employee number input aliases. Persist and export the seven-digit identity."""
import re


def employee_number(value) -> str:
    text = str(value if value is not None else '').strip()
    return text[1:] if re.fullmatch(r'0[0-9]{7}', text) else text


def employee_number_cell(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return employee_number(value)


def employee_number_aliases(value) -> tuple[str, ...]:
    number = employee_number(value)
    return (number, '0' + number) if re.fullmatch(r'[0-9]{7}', number) else (number,)
