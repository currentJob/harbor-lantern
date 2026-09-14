"""테스트 패키지.

`tests/` 를 패키지로 두는 이유는 `from tests.fakes import FakeWeatherPort` 가
하위 디렉토리(`tests/domain`·`tests/api`·`tests/static`) 어디에서나 같은 모듈을
가리키게 하기 위함이다. `pyproject.toml` 의 `pythonpath = ["."]` 가 짝이다.
"""
