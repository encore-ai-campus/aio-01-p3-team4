"""한글 마지막 글자의 받침에 맞춰 조사를 붙인다."""


def with_josa(word, pair):
    consonant, vowel = pair.split("/")
    if not word or not "가" <= word[-1] <= "힣":
        return f"{word}{consonant}({vowel})"
    final = (ord(word[-1]) - ord("가")) % 28
    return word + (consonant if final and not (consonant == "으로" and final == 8) else vowel)
