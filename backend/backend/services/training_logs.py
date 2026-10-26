"""Render bounded Docker terminal output as readable text without ANSI escapes."""
import re

TOKENS=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\r|\n|\b|[^\x1b\r\n\b]+')


def normalize_log(text):
    lines=[''];row=column=0
    for token in TOKENS.findall(text[-160000:]):
        if token=='\r':column=0
        elif token=='\n':
            row+=1;column=0
            if row==len(lines):lines.append('')
        elif token=='\b':column=max(0,column-1)
        elif token.startswith('\x1b['):
            if token.endswith('A'):row=max(0,row-int(token[2:-1] or 1))
            elif token.endswith('K'):lines[row]=lines[row][:column]
        elif not token.startswith('\x1b'):
            line=lines[row];lines[row]=line[:column].ljust(column)+token+line[column+len(token):];column+=len(token)
    return '\n'.join(line.rstrip() for line in lines).strip('\n')
