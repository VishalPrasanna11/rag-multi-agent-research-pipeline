import logging
import re
from typing import Dict, Any, List, Optional
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


class PDFParser:
    def extract_text(self, pdf_path: str) -> str:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text

    def extract_earnings_metrics(self, text: str) -> Dict[str, Any]:
        metrics: Dict[str, Any] = {}

        revenue_patterns = [
            r"(?:revenue|net revenue|total revenue)[:\s]*\$?([\d,]+\.?\d*)\s*(million|billion|M|B)",
            r"\$?([\d,]+\.?\d*)\s*(million|billion|M|B)\s*(?:in revenue|revenue)",
        ]
        for pattern in revenue_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = float(match.group(1).replace(",", ""))
                unit = match.group(2).lower()
                if unit in ("billion", "b"):
                    value *= 1000
                metrics["revenue_millions"] = value
                break

        eps_patterns = [
            r"(?:earnings per share|EPS|diluted EPS)[:\s]*\$?([\d]+\.?\d*)",
            r"\$?([\d]+\.?\d*)\s*(?:per diluted share|earnings per share)",
        ]
        for pattern in eps_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                metrics["eps"] = float(match.group(1))
                break

        gm_patterns = [
            r"(?:gross margin|gross profit margin)[:\s]*([\d]+\.?\d*)%",
        ]
        for pattern in gm_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                metrics["gross_margin_pct"] = float(match.group(1))
                break

        guidance_section = re.search(
            r"(?:outlook|guidance|forward.looking|expects?)[:\s]*(.*?)(?:\n\n|\Z)",
            text, re.IGNORECASE | re.DOTALL,
        )
        if guidance_section:
            metrics["guidance_text"] = guidance_section.group(1).strip()[:500]

        return metrics

    def parse_earnings_release(self, pdf_path: str) -> Dict[str, Any]:
        text = self.extract_text(pdf_path)
        metrics = self.extract_earnings_metrics(text)
        return {
            "full_text": text,
            "metrics": metrics,
            "page_count": fitz.open(pdf_path).page_count,
        }

    def chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunk = " ".join(words[start:end])
            chunks.append(chunk)
            start += chunk_size - overlap
        return chunks
