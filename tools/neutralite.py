"""
Garde anti-opinion pour les tweets Presto.

La marque repose sur « les faits, pas le sermon » : aucun tweet ne doit
contenir d'adjectif/adverbe évaluatif qui trahirait une opinion. Ce module
est la ceinture-et-bretelles : le prompt LLM demande déjà la neutralité, mais
on vérifie ici par regex avant publication. Si un mot interdit passe, on
attrape le tweet au lieu de se fier uniquement au modèle.

Usage :
    from neutralite import mots_evaluatifs
    fautes = mots_evaluatifs(texte)
    if fautes:
        ...  # rejeter / régénérer
"""

import re
import unicodedata

# Bases évaluatives. On matche chaque base + ses flexions courantes
# (féminin -e, pluriel -s/-es). Garder la liste *high-signal* pour éviter
# de bloquer des faits neutres légitimes.
_BASES_EVALUATIVES = [
    # adjectifs de jugement
    "inquiétant", "rassurant", "alarmant", "encourageant", "décevant",
    "choquant", "scandaleux", "révoltant", "indigne", "honteux",
    "catastrophique", "désastreux", "dramatique", "terrible", "tragique",
    "prometteur", "impressionnant", "remarquable", "formidable", "génial",
    "brillant", "inacceptable", "regrettable", "déplorable", "préoccupant",
    "spectaculaire", "incroyable", "stupéfiant", "effarant", "ahurissant",
    # comparatifs de valeur
    "pire", "meilleur", "mieux",
    # adverbes d'opinion
    "heureusement", "malheureusement", "hélas", "enfin",
]

# Mots qui ressemblent à une base mais sont neutres dans certains contextes :
# on ne les bloque PAS (laissés hors liste volontairement). Ex. « important »,
# « majeur », « historique » sont factuels-descriptifs et tolérés.

def _pattern(base: str) -> str:
    # autorise les flexions : inquiétant -> inquiétant(e)(s), pire -> pire(s)
    if base.endswith("eur"):           # meilleur -> meilleur(e)(s)
        suff = r"e?s?"
    elif base.endswith(("ant", "é", "eux", "if", "al", "ique", "ble", "aire")):
        suff = r"(e|s|es)?"
    else:
        suff = r"s?"
    return r"\b" + re.escape(base) + suff + r"\b"


_REGEX = re.compile("|".join(_pattern(b) for b in _BASES_EVALUATIVES), re.IGNORECASE)


def mots_evaluatifs(texte: str) -> list[str]:
    """Retourne la liste des mots évaluatifs trouvés (vide = texte neutre)."""
    return [m.group(0) for m in _REGEX.finditer(texte)]


def est_neutre(texte: str) -> bool:
    return not mots_evaluatifs(texte)


if __name__ == "__main__":
    # mini auto-test
    cas = [
        ("Hausse de 25 % des tarifs sur l'acier canadien.", True),
        ("Heureusement, la situation s'améliore.", False),
        ("Une décision catastrophique pour le Québec.", False),
        ("Ottawa confirme une exemption en négociation.", True),
        ("Les pires craintes des propriétaires se confirment.", False),
    ]
    for txt, attendu in cas:
        ok = est_neutre(txt) == attendu
        flag = "OK " if ok else "ÉCHEC"
        print(f"[{flag}] {txt!r} -> {mots_evaluatifs(txt)}")
