"""BGE-M3 稠密向量：从本地目录加载模型，只用 CPU，不联网，不往模型目录里写东西。

只有 MCP Server 和导入知识库的脚本会用到。torch 在真正加载模型时才导入（要好几秒），后端进程和测试都不受影响。
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path

from app.business.knowledge import Embedder, TextTooLongError
from app.config import Settings
from app.models import EMBEDDING_DIMENSION

MAX_TOKENS = 1024  # 一段文本最多多少个 token（按 BGE-M3 的分词器算，含特殊 token）。超过就报错，不截断

# 用来算模型指纹的小文件（配置和分词器）。权重文件有 2GB 多，每次启动都算哈希太慢，只取它的大小
FINGERPRINT_FILES = (
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "1_Pooling/config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
)
WEIGHTS_FILE = "pytorch_model.bin"


class EmbeddingModelError(Exception):
    """模型目录不对，或者加载出来的不是需要的模型。消息里写明问题在哪。"""


class BgeM3Embedder:
    """用 sentence-transformers 加载的 BGE-M3，输出归一化的 1024 维稠密向量。"""

    def __init__(self, model_dir: Path) -> None:
        missing = [name for name in (*FINGERPRINT_FILES, WEIGHTS_FILE) if not (model_dir / name).is_file()]
        if missing:
            raise EmbeddingModelError(
                f"EMBEDDING_MODEL_DIR={model_dir} 不是完整的 BGE-M3 模型目录，缺少：{', '.join(missing)}"
            )

        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(str(model_dir), device="cpu", local_files_only=True)
        dimension = self._model.get_embedding_dimension()
        if dimension != EMBEDDING_DIMENSION:
            raise EmbeddingModelError(f"{model_dir} 里的模型输出 {dimension} 维向量，需要 {EMBEDDING_DIMENSION} 维")
        self._model.max_seq_length = MAX_TOKENS
        self.signature = (
            f"bge-m3:{_fingerprint(model_dir)};dim={EMBEDDING_DIMENSION};normalized;max_tokens={MAX_TOKENS}"
        )
        # 第一次编码要做一些初始化，放在加载时做掉，第一个顾客的查询不会偏慢
        self._model.encode(["预热"], show_progress_bar=False)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        for index, text in enumerate(texts):
            tokens = len(self._model.tokenizer(text)["input_ids"])
            if tokens > MAX_TOKENS:
                raise TextTooLongError(index, tokens, MAX_TOKENS)
        vectors = self._model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()


def load_embedder(settings: Settings) -> Embedder | None:
    """按配置加载向量器：关键词模式返回 None；混合模式加载 BGE-M3，大约要十几秒。"""
    if settings.retrieval_mode == "keyword":
        return None
    return BgeM3Embedder(Path(settings.embedding_model_dir))


def _fingerprint(model_dir: Path) -> str:
    """模型指纹：配置和分词器文件的内容，加上权重文件的大小。换了模型或版本，指纹就会变。"""
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((model_dir / name).read_bytes())
    digest.update(str((model_dir / WEIGHTS_FILE).stat().st_size).encode())
    return digest.hexdigest()[:16]
