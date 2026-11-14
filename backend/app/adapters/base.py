from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.file_reader import read_tabular_file
from app.models.transaction import SourceTransaction


class AdapterError(ValueError):
    """Raised when a source report does not have its expected schema."""


class BaseSourceAdapter:
    source_type = "source"
    required_columns: tuple[str, ...] = ()

    def read(self, path: str) -> tuple[pd.DataFrame, list[SourceTransaction]]:
        frame = read_tabular_file(path)
        self.validate_columns(frame)
        return frame, self.normalize(frame)

    def validate_columns(self, frame: pd.DataFrame) -> None:
        actual = {str(column).strip().casefold() for column in frame.columns}
        missing = [c for c in self.required_columns if c.casefold() not in actual]
        if missing:
            raise AdapterError(
                f"{self.source_type} report is missing required columns: {', '.join(missing)}"
            )

    @staticmethod
    def row_dict(row: pd.Series) -> dict[str, Any]:
        return {str(key): (None if value == "" else value) for key, value in row.items()}

    def normalize(self, frame: pd.DataFrame) -> list[SourceTransaction]:
        raise NotImplementedError
