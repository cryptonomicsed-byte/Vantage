rule embedded_php_tag
{
    meta:
        description = "Detects a PHP opening tag embedded inside a file that should be a plain image/video/audio artifact — the classic polyglot upload payload"
    strings:
        $php_open = "<?php"
        $php_short = "<?="
    condition:
        any of them
}

rule embedded_script_tag
{
    meta:
        description = "Detects an executable <script> tag inside a non-HTML artifact (e.g. an SVG-as-image upload)"
    strings:
        $script = "<script" nocase
    condition:
        $script
}
