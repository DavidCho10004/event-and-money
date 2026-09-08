"""
DB 세션 관리 회귀 테스트 (CLAUDE.md 역할 5)

배경: backend/main.py의 라우터들이 SessionLocal()을 직접 만들고
      finally 없이 db.close()를 호출해, 예외 발생 시 세션이 반납되지
      않는 구조였다. SQLite에서는 드러나지 않지만 PostgreSQL 전환 시
      커넥션 풀 고갈로 이어진다.

이 테스트는 그 구조가 다시 들어오는 것을 막는다:
  1. main.py에 수동 세션 생성/종료 코드가 없을 것
  2. DB를 쓰는 모든 라우터가 Depends(get_db)로 세션을 받을 것

실행: pytest tests/test_db_session.py -v
"""
import ast
from pathlib import Path

MAIN_PY = Path(__file__).resolve().parent.parent / "backend" / "main.py"
SOURCE = MAIN_PY.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _route_functions():
    """@app.get/@app.post 등이 붙은 최상위 함수만 추출"""
    for node in TREE.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # @app.get("/...") 형태
            func = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                    and func.value.id == "app":
                yield node
                break


def test_no_manual_session_in_main():
    """SessionLocal()을 직접 호출하지 않는다 — get_db 의존성만 사용"""
    assert "SessionLocal(" not in SOURCE, (
        "backend/main.py에서 SessionLocal()을 직접 호출하고 있습니다. "
        "Depends(get_db)를 쓰세요."
    )


def test_no_manual_close_in_main():
    """db.close()를 직접 호출하지 않는다 — get_db의 finally가 반납한다"""
    assert "db.close()" not in SOURCE, (
        "backend/main.py에서 db.close()를 직접 호출하고 있습니다. "
        "예외 시 실행되지 않아 세션이 샙니다. get_db에 맡기세요."
    )


def test_db_routes_use_dependency():
    """db를 쓰는 라우터는 반드시 db 인자를 Depends(get_db)로 받는다"""
    offenders = []
    for fn in _route_functions():
        body_src = ast.get_source_segment(SOURCE, fn) or ""
        uses_db = "db." in body_src
        arg_names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
        if uses_db and "db" not in arg_names:
            offenders.append(fn.name)
    assert not offenders, (
        f"db를 쓰면서 Depends(get_db)를 받지 않는 라우터: {offenders}"
    )


def test_get_db_closes_in_finally():
    """get_db 자체가 finally에서 세션을 닫는지 확인"""
    db_py = MAIN_PY.parent / "db" / "database.py"
    src = db_py.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "get_db")
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
    assert tries and any(t.finalbody for t in tries), \
        "get_db()가 finally 블록에서 세션을 닫아야 합니다."
