from homebot.services.wordmatch import matches


def test_matching_by_word_start_and_all_words():
    assert matches("Nvidia анонсировала RTX 5070 Ti в Украине", "RTX 5070")
    assert not matches("Nvidia RTX 5080", "RTX 5070")  # число — точное
    assert not matches("RTX 50700", "RTX 5070")
    assert matches("В Кривом Роге объявили тревогу", "Кривой Рог")  # окончания не мешают
    assert not matches("Кривой путь", "Кривой Рог")  # нужны ВСЕ слова
    assert not matches("рогатый олень", "Рог")  # короткое слово — почти точно
    assert matches("Вышел Jellyfin 10.11 с новым плеером", "jellyfin")
    assert matches("JELLYFIN update", "Jellyfin")
    assert matches("AMD Ryzen 9 9950X", "ryzen 9")
    assert not matches("anything", "   ")  # пустой запрос не подходит ко всему подряд
