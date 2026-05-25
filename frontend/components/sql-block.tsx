'use client';

import { useMemo } from 'react';
import hljs from 'highlight.js/lib/core';
import sql from 'highlight.js/lib/languages/sql';

hljs.registerLanguage('sql', sql);

interface SqlBlockProps {
  code: string;
}

export function SqlBlock({ code }: SqlBlockProps) {
  const highlighted = useMemo(() => {
    if (!code) return '';
    try {
      return hljs.highlight(code, { language: 'sql' }).value;
    } catch {
      return code
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    }
  }, [code]);

  return (
    <pre style={{ margin: 0, padding: '12px', fontSize: '12px', lineHeight: '1.6', background: 'transparent', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      <code dangerouslySetInnerHTML={{ __html: highlighted }} />
    </pre>
  );
}
