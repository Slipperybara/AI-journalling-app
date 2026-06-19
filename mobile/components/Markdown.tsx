import { type ReactNode } from 'react';
import { Text, type TextStyle, View } from 'react-native';

import { fonts } from '../lib/theme';

// Same soft-yellow marker the onboarding story beats use, so highlighting reads
// consistently across the app (was a warmer orange before).
const HIGHLIGHT_BG = 'rgba(232, 191, 90, 0.38)';

// Inline tokens, in precedence order: **bold**, ==highlight==, *italic*, _italic_.
const INLINE = /(\*\*[^*]+\*\*|==[^=]+==|\*[^*\n]+\*|_[^_\n]+_)/g;

function renderInline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  INLINE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (tok.startsWith('**')) {
      out.push(
        <Text key={key} style={{ fontFamily: fonts.serifSemiBold }}>
          {tok.slice(2, -2)}
        </Text>,
      );
    } else if (tok.startsWith('==')) {
      // Marker-pen highlight. Each word carries its OWN trailing space inside the
      // backed <Text>, so within a line the stroke reads as one uniform mark
      // (no ugly gaps between words). RN breaks between these word-Texts and
      // collapses the trailing space at a soft wrap, so no full-width yellow bar
      // forms at line breaks. Hard '\n' is emitted un-highlighted between lines.
      tok
        .slice(2, -2)
        .split('\n')
        .forEach((line, li) => {
          if (li > 0) out.push('\n');
          const segs = line.split(/(\s+)/); // [word, ws, word, ws, …]
          for (let s = 0; s < segs.length; s += 2) {
            const chunk = (segs[s] ?? '') + (segs[s + 1] ?? '');
            if (chunk === '') continue;
            out.push(
              <Text key={`${key}-${li}-${s}`} style={{ backgroundColor: HIGHLIGHT_BG }}>
                {chunk}
              </Text>,
            );
          }
        });
    } else {
      out.push(
        <Text key={key} style={{ fontFamily: fonts.serifItalic }}>
          {tok.slice(1, -1)}
        </Text>,
      );
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Lightweight markdown for the chat canvas. Supports paragraphs, bullet lists
// (- / *), and inline bold / italic / highlight. A custom renderer (rather than
// a library) because our serif weights are distinct font families, not
// fontWeight — so bold must map to Lora SemiBold, not fontWeight: 'bold'.
export function Markdown({ content, style }: { content: string; style: TextStyle }) {
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let k = 0;

  const flush = () => {
    if (!para.length) return;
    blocks.push(
      <Text key={`p-${k++}`} style={[style, blocks.length ? { marginTop: 10 } : null]}>
        {renderInline(para.join('\n'), `p-${k}`)}
      </Text>,
    );
    para = [];
  };

  content.split('\n').forEach((line, idx) => {
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      flush();
      blocks.push(
        <View key={`li-${idx}`} style={{ flexDirection: 'row', marginTop: 3 }}>
          <Text style={[style, { width: 18 }]}>{'•'}</Text>
          <Text style={[style, { flex: 1 }]}>{renderInline(bullet[1], `li-${idx}`)}</Text>
        </View>,
      );
    } else if (line.trim() === '') {
      flush();
    } else {
      para.push(line);
    }
  });
  flush();

  return <View>{blocks}</View>;
}
