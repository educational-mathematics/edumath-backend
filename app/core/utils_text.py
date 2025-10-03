def neutralize_audio_words(question: str) -> str:
    if not question:
        return question
    q = question.strip()
    lowers = q.lower()
    if lowers.startswith("escucha ") or "escucha atentamente" in lowers or "te dictan" in lowers:
        return (
            q.replace("Escucha atentamente ", "Lee atentamente ")
                .replace("Escucha ", "Lee ")
                .replace("te dictan", "se presentan")
        )
    return q
