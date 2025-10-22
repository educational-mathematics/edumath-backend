from typing import Protocol, List, Dict, Any, Optional, Tuple

class TopicEngine(Protocol):
    """
    Un engine por tema: construye 10 ítems, sanea, y valida respuestas.
    No genera imagen/audio aquí (eso lo hará el router según el estilo).
    """

    def build_session(
        self,
        context_json: Dict[str, Any],
        style: str,
        avoid_numbers: Optional[List[Tuple[int, int]]] = None,
        seed: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Retorna:
        - 'items': List[Dict]  (10 ítems como espera tu front)
        - 'explanation': str   (texto breve)
        - 'meta': Dict         (opcional)
        """
        ...

    def sanitize_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normaliza ítems (rellena opciones MCQ, corrige índices, etc.)."""
        ...

    def check_answer(self, item: Dict[str, Any], answer: Any) -> bool:
        """Valida respuesta de un ítem individual."""
        ...
        
    def validate_repair(self, items: List[Dict[str, Any]], context_json: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Hook para sanear ítems ya generados/persistidos. Por defecto, no hace nada."""
        return items