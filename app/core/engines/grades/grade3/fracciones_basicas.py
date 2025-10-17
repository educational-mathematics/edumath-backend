from typing import Dict, Any, List, Tuple, Optional
from app.ai.gemini import generate_exercises_variant, generate_explanation
from app.core.engines.base import TopicEngine

class FraccionesBasicasEngine(TopicEngine):
    """
    Engine para el tema 'fracciones-basicas' (grade 3).
    Usa tus funciones AI (con fallback interno en gemini.py), y aplica sanitización.
    """

    def build_session(
        self,
        context_json: Dict[str, Any],
        style: str,
        avoid_numbers: Optional[List[Tuple[int, int]]] = None,
        seed: Optional[int] = None
    ) -> Dict[str, Any]:
        explanation = generate_explanation(context_json)
        items = generate_exercises_variant(context_json, style, avoid_numbers or [])
        items = self.sanitize_items(items)
        if len(items) > 10:
            items = items[:10]
        return {
            "items": items,
            "explanation": explanation,
            "meta": {"topic_kind": "fracciones_basicas"}
        }

    def sanitize_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Aquí podrías mover la lógica que tenías en el router:
        - completar opciones de MCQ hasta 4
        - recalcular correct_index por valor cuando cambias el orden
        - normalizar tipos
        Para no inferir, dejamos el passthrough (ya que gemini.py intenta devolver ítems consistentes).
        """
        return items

    def check_answer(self, item: Dict[str, Any], answer: Any) -> bool:
        t = item.get("type") or item.get("kind") or "multiple_choice"
        if t == "multiple_choice":
            try:
                return int(answer) == int(item.get("correct_index", -1))
            except Exception:
                return False
        elif t == "match_pairs":
            # comparación exacta de pares
            return answer == item.get("pairs")
        elif t == "drag_to_bucket":
            sol = item.get("solution", {})
            if not isinstance(answer, dict) or not isinstance(sol, dict):
                return False
            return all(set(answer.get(b, [])) == set(sol.get(b, [])) for b in sol.keys())
        else:
            return False