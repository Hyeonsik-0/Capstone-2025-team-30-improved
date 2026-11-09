"""설치된 패키지 목록(pip freeze --all)이 lock 파일들과 정확히 일치하는지 확인한다. (이슈 #3)

사용법: python compare_lock.py <pip freeze --all 결과 파일> <lock 파일> [<lock 파일> ...]
- lock에 있는 패키지가 같은 버전으로 모두 설치되어 있어야 한다.
- lock에 없는 패키지는 베이스 이미지에 포함된 pip만 허용한다.
"""
import re
import sys

BASE_TOOLS = {"pip"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(path: str) -> dict:
    pins = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", line)
        if m:
            pins[norm(m.group(1))] = m.group(2)
    return pins


installed = parse(sys.argv[1])
lock = {}
for path in sys.argv[2:]:
    lock.update(parse(path))

problems = []
for name, ver in sorted(lock.items()):
    if name not in installed:
        problems.append(f"누락: {name}=={ver}")
    elif installed[name] != ver:
        problems.append(f"버전 불일치: {name} lock={ver} 설치={installed[name]}")
for name in sorted(set(installed) - set(lock) - BASE_TOOLS):
    problems.append(f"lock에 없는 패키지: {name}=={installed[name]}")

print(f"lock 패키지 {len(lock)}개, 설치 패키지 {len(installed)}개")
if problems:
    print("\n".join(problems))
    sys.exit(1)
print("설치 목록이 lock과 일치")
