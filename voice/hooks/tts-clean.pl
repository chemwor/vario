#!/usr/bin/perl -0777 -p
# Markdown cleanup + ~500-char sentence-boundary truncation for spoken text (stdin -> stdout).
  s/^[ \t]*```.*?^[ \t]*```[^\n]*\n?//gms;   # fenced code blocks + contents
  s/^[ \t]*```.*\z//ms;                      # unterminated fence at end
  s{`([^`\n]{1,40})`}{my $c = $1; $c =~ s/\(.*?\)//g; $c =~ tr/_/ /; $c}ge;   # short inline code: keep the words, drop (args)
  s/`[^`\n]*`//g;                          # long inline code: drop
  s/!?\[([^\]]*)\]\([^)]*\)/$1/g;            # links/images -> label text
  s{\b(?:https?|ftp)://\S+}{}g;              # bare URLs
  s{(?<![\w/])(?:~|\.{1,2})?/?(?:[\w.\-@]+/)+[\w.\-@]*(?::\d+)?}{}g;  # file paths
  s/^[ \t]{0,3}#{1,6}[ \t]*//gm;             # headers
  s/^[ \t]*>[ \t]?//gm;                      # blockquotes
  s/^[ \t]*(?:[-*+]|\d+[.)])[ \t]+//gm;      # bullets / numbered lists
  s/^[ \t]*\|.*\|[ \t]*$//gm;                # table rows
  s/^[ \t]*[-*_]{3,}[ \t]*$//gm;             # horizontal rules
  s/(\*\*|~~|\*)(?=\S)(.+?)(?<=\S)\1/$2/g;   # bold/italic/strike
  s/(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)/$2/g;   # _italic_ only at word boundaries (keeps snake_case)
  s/[*#`|]//g;                               # leftover symbols
  s/[ \t]+/ /g;
  s/([^.!?:;,\s])[ \t]*\n/$1. /g;            # line ends become pauses
  s/\s*\n\s*/ /g;
  s/\.(?:\s*\.)+/./g;
  s/\s+([.,;:!?])/$1/g;
  s/^[\s.]+|\s+$//g;
  if (length($_) > 500) {
    my $cut = substr($_, 0, 500);
    if ($cut =~ /\A(.*[.!?])(?:\s|\z)/s && length($1) >= 150) { $_ = $1 }
    else { $cut =~ s/\s+\S*\z//; $_ = "$cut..." }
  }
