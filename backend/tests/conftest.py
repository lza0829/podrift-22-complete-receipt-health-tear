"""测试引导：必须在任何 app 模块导入前指向独立临时库，
避免碰到仓库 data/podrift.db 或被 import-time seed 污染真实环境。"""
import os
import pathlib
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="podrift_test_")
os.environ["PODRIFT_DB"] = str(pathlib.Path(_TMP_DIR) / "test.db")
