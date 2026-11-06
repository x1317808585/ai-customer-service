"""测试用的向量器，不加载模型。知识库的几个测试共用。"""

import zlib
from collections.abc import Sequence

from app.business.knowledge import TextTooLongError
from app.models import EMBEDDING_DIMENSION


class FakeEmbedder:
    """含有“蓝牙”或“无线”的文本落在同一个方向上（相似度 1），其余文本各自一个方向（相似度 0）。

    超过 MAX_CHARS 个字就当作超长。
    """

    signature = "fake;dim=1024"
    MAX_CHARS = 100

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for index, passage_text in enumerate(texts):
            if len(passage_text) > self.MAX_CHARS:
                raise TextTooLongError(index, len(passage_text), self.MAX_CHARS)
            same_topic = "蓝牙" in passage_text or "无线" in passage_text
            axis = 1 if same_topic else 2 + zlib.crc32(passage_text.encode()) % 1000
            vector = [0.0] * EMBEDDING_DIMENSION
            vector[axis] = 1.0
            vectors.append(vector)
        return vectors
