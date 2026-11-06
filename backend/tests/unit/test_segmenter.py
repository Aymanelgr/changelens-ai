from app.modules.extraction.segmenter import MAX_SEGMENT_CHARS, segment_markdown, segment_text


def _assert_offsets_exact(source, segments):
    for segment in segments:
        assert source[segment.start_char : segment.end_char] == segment.text


def test_section_paths_follow_heading_levels():
    source = "# Guide\nIntro\n## 3. Opérations\n### 3.2 Virements\nTexte\n## 4. Fin\nBye\n"
    segments = segment_markdown(source)
    paths = [s.section_path for s in segments]
    assert paths == [
        "Guide",
        "Guide > 3. Opérations",
        "Guide > 3. Opérations > 3.2 Virements",
        "Guide > 4. Fin",
    ]
    _assert_offsets_exact(source, segments)


def test_heading_inside_code_fence_is_not_a_section():
    source = "# Titre\n```\n# pas un titre\n```\nFin\n"
    segments = segment_markdown(source)
    assert [s.section_path for s in segments] == ["Titre"]


def test_long_section_is_split_on_paragraphs_with_exact_offsets():
    paragraph = "mot " * 200
    source = "# Long\n\n" + "\n\n".join([paragraph] * 5)
    segments = segment_markdown(source)
    assert len(segments) > 1
    assert all(s.section_path == "Long" for s in segments)
    assert all(len(s.text) <= MAX_SEGMENT_CHARS for s in segments)
    _assert_offsets_exact(source, segments)


def test_windows_line_endings_keep_offsets_exact():
    source = "# Titre\r\n\r\nUn.\r\n\r\n## Deux\r\nTexte.\r\n"
    segments = segment_markdown(source)
    assert [s.section_path for s in segments] == ["Titre", "Titre > Deux"]
    _assert_offsets_exact(source, segments)


def test_text_is_split_by_paragraph():
    source = "Un.\n\nDeux.\n  \nTrois.\n"
    segments = segment_text(source)
    assert [s.text for s in segments] == ["Un.", "Deux.", "Trois."]
    assert all(s.section_path is None for s in segments)
    _assert_offsets_exact(source, segments)
