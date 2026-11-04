"""Invoke the supplied Java signer without a shell or exposing its output in logs."""
import json
import os
from pathlib import Path
import subprocess

from fastapi import HTTPException

DEFAULT_JAR = Path(__file__).resolve().parents[1] / 'license-tool' / 'LicenseTool-1.0-SNAPSHOT-jar-with-dependencies.jar'


def generate_key(tenant_id, expire_time, account_number=-1, agent_number=-1):
    jar = Path(os.getenv('LICENSE_TOOL_JAR', str(DEFAULT_JAR)))
    if not jar.is_file():
        raise HTTPException(503, 'License 生成工具未安装，请联系管理员配置 JAR 文件')
    command = [os.getenv('JAVA_BIN', 'java'), '-jar', str(jar),
               '-tenantId', str(tenant_id), '-expireTime', str(expire_time),
               '-accountNumber', str(account_number), '-agentNumber', str(agent_number)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                cwd=jar.parent, check=False)
    except FileNotFoundError:
        raise HTTPException(503, '未找到 Java 运行环境，请联系管理员安装 Java 17 或更高版本')
    except subprocess.TimeoutExpired:
        raise HTTPException(504, 'License 生成超时，请稍后重试')
    except OSError:
        raise HTTPException(503, 'License 生成工具暂时不可用，请联系管理员检查运行环境')
    if result.returncode != 0:
        raise HTTPException(502, 'License 生成失败，请联系管理员检查 Java 和生成工具')
    try:
        payload = json.loads(result.stdout)
        license_data = payload['license']
        if not all(isinstance(license_data[k], str) and license_data[k].strip() for k in ('body', 'key')):
            raise ValueError('invalid license output')
    except (ValueError, KeyError, TypeError):
        raise HTTPException(502, 'License 工具未返回有效的授权结果，请联系管理员检查')
    # Both the signed body and its key are required by the product.
    return json.dumps(payload, ensure_ascii=False, indent=2)
