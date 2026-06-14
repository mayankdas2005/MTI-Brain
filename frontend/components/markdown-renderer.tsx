'use client';

import ReactMarkdown, { type Components } from 'react-markdown';
import type { PluggableList } from 'unified';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { ReactNode, useState, memo, useDeferredValue, useMemo } from 'react';
import React from 'react';
import { Button } from '@/components/ui/button';
import {
  Table, TableHeader, TableBody, TableHead, TableRow, TableCell,
} from '@/components/ui/table';
import {
  Copy, Check, ArrowUp, ArrowDown, ArrowUpDown,
  AlertTriangle, AlertCircle, Info, Lightbulb,
} from 'lucide-react';
import { copyText } from '@/lib/utils';
import { toast } from '@/lib/toast';

interface MarkdownRendererProps {
  content: string;
  isUser?: boolean;
}

interface CodeBlockProps {
  children: ReactNode;
  className?: string;
}

const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code || []), ['className', /^language-/, /^hljs/]],
    span: [...(defaultSchema.attributes?.span || []), ['className', /^hljs/]],
    pre: [...(defaultSchema.attributes?.pre || []), 'className'],
  },
};

const remarkPlugins: PluggableList = [remarkGfm];
const rehypePlugins: PluggableList = [[rehypeSanitize, sanitizeSchema], rehypeHighlight];

// ─── Alert / callout system ───────────────────────────────────────────────────

type AlertVariant = 'warning' | 'error' | 'info' | 'success';

const ALERT_STYLES: Record<AlertVariant, {
  container: string;
  border: string;
  icon: React.ReactNode;
}> = {
  warning: {
    container: 'bg-amber-50 dark:bg-amber-950/25',
    border: 'border-amber-400 dark:border-amber-500',
    icon: <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />,
  },
  error: {
    container: 'bg-red-50 dark:bg-red-950/25',
    border: 'border-red-400 dark:border-red-500',
    icon: <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />,
  },
  info: {
    container: 'bg-blue-50 dark:bg-blue-950/25',
    border: 'border-blue-400 dark:border-blue-500',
    icon: <Info className="w-4 h-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />,
  },
  success: {
    container: 'bg-green-50 dark:bg-green-950/25',
    border: 'border-green-400 dark:border-green-500',
    icon: <Lightbulb className="w-4 h-4 text-green-600 dark:text-green-400 shrink-0 mt-0.5" />,
  },
};

function extractText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (node && typeof node === 'object' && 'props' in (node as object)) {
    const el = node as React.ReactElement<{ children?: ReactNode }>;
    return extractText(el.props.children);
  }
  return '';
}

function detectBlockquoteVariant(children: ReactNode): AlertVariant | null {
  const text = extractText(children).trim();

  // GitHub-style [!NOTE], [!WARNING], etc.
  const ghMatch = text.match(/^\[!(WARNING|NOTE|TIP|IMPORTANT|CAUTION)\]/i);
  if (ghMatch) {
    const kw = ghMatch[1].toLowerCase();
    if (['warning', 'caution'].includes(kw)) return 'warning';
    if (['note', 'tip'].includes(kw)) return 'info';
    if (kw === 'important') return 'error';
  }

  const firstWord = text.split(/[\s\n:,!]/)[0].toLowerCase();
  if (['warning', 'concern', 'caution', 'data'].includes(firstWord)) return 'warning';
  if (['error', 'critical', 'alert', 'danger'].includes(firstWord)) return 'error';
  if (['note', 'info', 'information', 'tip'].includes(firstWord)) return 'info';
  if (['success', 'good', 'passed'].includes(firstWord)) return 'success';
  return null;
}


// ─── Code block ───────────────────────────────────────────────────────────────

function CodeBlock({ children, className }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const code = String(children).replace(/\n$/, '');

  const copyCode = async () => {
    const ok = await copyText(code);
    if (!ok) {
      toast.error('Copy failed', { id: 'copy-failed' });
      return;
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative bg-muted rounded-lg overflow-hidden mb-4 group">
      <pre className="p-3 overflow-x-auto text-sm font-mono">
        <code className={className}>{children}</code>
      </pre>
      <Button
        size="sm"
        variant="ghost"
        aria-label={copied ? 'Copied' : 'Copy code'}
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity"
        onClick={copyCode}
      >
        {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
      </Button>
    </div>
  );
}

// ─── HAST node types (subset used for table parsing) ─────────────────────────

interface HastText { type: 'text'; value: string }
interface HastElement {
  type: 'element';
  tagName: string;
  properties?: Record<string, unknown>;
  children: HastNode[];
}
type HastNode = HastText | HastElement | { type: string };

function hastToText(node: HastNode): string {
  if (node.type === 'text') return (node as HastText).value;
  if ('children' in node) return (node as HastElement).children.map(hastToText).join('');
  return '';
}

function hastToReact(node: HastNode, key: number): ReactNode {
  if (node.type === 'text') return (node as HastText).value;
  if (node.type === 'element') {
    const el = node as HastElement;
    const ch = el.children.map((c, i) => hastToReact(c, i));
    switch (el.tagName) {
      case 'strong': {
        const txt = hastToText(el);
        const isMetric = txt.trim().split(/\s+/).filter(Boolean).length <= 4 && /\d/.test(txt);
        return isMetric
          ? <span key={key} className="inline-flex items-center font-mono font-semibold text-[0.85em] bg-muted/60 border border-border/70 rounded px-1.5 py-px mx-0.5 text-foreground whitespace-nowrap">{ch}</span>
          : <strong key={key} className="font-semibold text-foreground">{ch}</strong>;
      }
      case 'em': return <em key={key}>{ch}</em>;
      case 'del': return <del key={key}>{ch}</del>;
      case 'code': return (
        <code key={key} className="bg-muted px-1.5 py-0.5 rounded text-[0.9em] font-mono">{ch}</code>
      );
      case 'a': return (
        <a key={key} href={el.properties?.href as string | undefined} className="text-primary hover:underline">{ch}</a>
      );
      default: return <span key={key}>{ch}</span>;
    }
  }
  return null;
}

// ─── Sortable markdown table ──────────────────────────────────────────────────

function SortableMarkdownTable({ node: tableNode }: { node: HastElement }) {
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

  const { columns, rows } = useMemo(() => {
    const columns: string[] = [];
    const rows: { text: string[]; nodes: HastNode[][] }[] = [];

    for (const child of tableNode.children) {
      if (child.type !== 'element') continue;
      const section = child as HastElement;

      if (section.tagName === 'thead') {
        const tr = section.children.find(
          n => n.type === 'element' && (n as HastElement).tagName === 'tr'
        ) as HastElement | undefined;
        if (tr) {
          for (const th of tr.children) {
            if (th.type === 'element' && (th as HastElement).tagName === 'th') {
              columns.push(hastToText(th));
            }
          }
        }
      }

      if (section.tagName === 'tbody') {
        for (const row of section.children) {
          if (row.type !== 'element' || (row as HastElement).tagName !== 'tr') continue;
          const cells = (row as HastElement).children.filter(
            n => n.type === 'element' && (n as HastElement).tagName === 'td'
          ) as HastElement[];
          rows.push({
            text: cells.map(hastToText),
            nodes: cells.map(td => td.children),
          });
        }
      }
    }
    return { columns, rows };
  }, [tableNode]);

  const sortedRows = useMemo(() => {
    if (sortCol === null) return rows;
    return [...rows].sort((a, b) => {
      const av = a.text[sortCol] ?? '';
      const bv = b.text[sortCol] ?? '';
      const an = parseFloat(av.replace(/[,\s$%]/g, ''));
      const bn = parseFloat(bv.replace(/[,\s$%]/g, ''));
      const numeric = !isNaN(an) && !isNaN(bn) && av.trim() !== '' && bv.trim() !== '';
      const cmp = numeric ? an - bn : av.localeCompare(bv, undefined, { sensitivity: 'base' });
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [rows, sortCol, sortDir]);

  const handleSort = (ci: number) => {
    if (sortCol === ci) {
      if (sortDir === 'asc') { setSortDir('desc'); }
      else { setSortCol(null); setSortDir('asc'); }
    } else {
      setSortCol(ci);
      setSortDir('asc');
    }
  };

  return (
    <div className="not-prose rounded-xl border border-border overflow-hidden mb-6 mt-2 shadow-sm [&_tbody_tr:nth-child(even)]:bg-muted/20">
      {rows.length > 0 && (
        <div className="px-4 py-2 border-b border-border/40 bg-muted/10 flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="font-medium tabular-nums">{rows.length} rows</span>
          <span className="opacity-40">·</span>
          <span className="font-medium tabular-nums">{columns.length} columns</span>
        </div>
      )}
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/40 border-b border-border">
              {columns.map((col, ci) => (
                <TableHead
                  key={ci}
                  className="whitespace-nowrap cursor-pointer select-none hover:bg-accent/60 transition-colors py-3 px-4"
                  onClick={() => handleSort(ci)}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-widest font-semibold text-muted-foreground">
                      {col}
                    </span>
                    <span className="text-muted-foreground/40 shrink-0">
                      {sortCol === ci
                        ? (sortDir === 'asc'
                          ? <ArrowUp className="w-2.5 h-2.5 text-foreground" />
                          : <ArrowDown className="w-2.5 h-2.5 text-foreground" />)
                        : <ArrowUpDown className="w-2.5 h-2.5" />}
                    </span>
                  </div>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedRows.map((row, ri) => (
              <TableRow key={ri} className="hover:bg-accent/30 transition-colors">
                {row.nodes.map((cellNodes, ci) => (
                  <TableCell key={ci} className="text-xs py-2.5 px-4 whitespace-nowrap">
                    {cellNodes.map((n, i) => hastToReact(n, i))}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// ─── Markdown component map ───────────────────────────────────────────────────

const markdownComponents: Components = {
  h1: ({ children }) => (
    <h1 className="text-2xl font-bold mt-0 mb-4 pb-3 border-b border-border">
      {children}
    </h1>
  ),

  h2: ({ children }) => (
    <h2 className="text-lg font-semibold mt-8 mb-3 pb-2 border-b border-border/50">
      {children}
    </h2>
  ),

  h3: ({ children }) => (
    <h3 className="text-sm font-semibold mt-6 mb-2 pl-3 border-l-2 border-primary/40 text-foreground">
      {children}
    </h3>
  ),

  h4: ({ children }) => (
    <h4 className="text-xs font-semibold mt-5 mb-1.5 uppercase tracking-wide text-muted-foreground">
      {children}
    </h4>
  ),

  h5: ({ children }) => (
    <h5 className="text-xs font-semibold mt-4 mb-1 text-muted-foreground">{children}</h5>
  ),

  h6: ({ children }) => (
    <h6 className="text-[11px] font-semibold mt-3 mb-1 text-muted-foreground/70">{children}</h6>
  ),

  p: ({ children }) => (
    <p className="mb-4 leading-[1.75] text-foreground/90">{children}</p>
  ),

  strong: ({ children }) => {
    const text = extractText(children as ReactNode).trim();
    const isMetric = text.split(/\s+/).filter(Boolean).length <= 4 && /\d/.test(text);
    if (isMetric) {
      return (
        <span className="inline-flex items-center font-mono font-semibold text-[0.85em] bg-muted/60 border border-border/70 rounded px-1.5 py-px mx-0.5 text-foreground whitespace-nowrap">
          {children}
        </span>
      );
    }
    return <strong className="font-semibold text-foreground">{children}</strong>;
  },

  em: ({ children }) => (
    <em className="italic text-foreground/80">{children}</em>
  ),

  ul: ({ children }) => (
    <ul className="list-disc pl-5 mb-4 space-y-1.5 text-foreground/90">{children}</ul>
  ),

  ol: ({ children }) => (
    <ol className="list-decimal pl-5 mb-4 space-y-1.5 text-foreground/90">{children}</ol>
  ),

  li: ({ children }) => (
    <li className="pl-1 leading-[1.7]">{children}</li>
  ),

  hr: () => (
    <hr className="my-8 border-0 border-t border-border/50" />
  ),

  code: ({ children, className }) => {
    const isBlock = /language-/.test(className || '');
    return isBlock ? (
      <CodeBlock className={className}>{children}</CodeBlock>
    ) : (
      <code className="bg-muted px-1.5 py-0.5 rounded text-[0.9em] font-mono text-foreground">
        {children}
      </code>
    );
  },

  pre: ({ children }) => <>{children}</>,

  blockquote: ({ children }) => {
    const variant = detectBlockquoteVariant(children);
    if (variant) {
      const style = ALERT_STYLES[variant];
      return (
        <div className={`flex gap-3 ${style.container} border-l-4 ${style.border} rounded-r-lg px-4 py-3.5 mb-5`}>
          <span className="shrink-0 mt-0.5">{style.icon}</span>
          <div className="flex-1 text-sm leading-[1.7] [&>p]:mb-2 [&>p:last-child]:mb-0 [&>p]:text-foreground/90">
            {children}
          </div>
        </div>
      );
    }
    return (
      <blockquote className="border-l-4 border-primary/30 pl-4 py-1 mb-4 italic text-muted-foreground">
        {children}
      </blockquote>
    );
  },

  a: ({ href, children }) => {
    const isExternal = !!href && /^https?:\/\//i.test(href);
    return (
      <a
        href={href}
        {...(isExternal ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
        className="text-primary hover:underline underline-offset-2"
      >
        {children}
      </a>
    );
  },

  table: ({ node }) => (
    <SortableMarkdownTable node={node as unknown as HastElement} />
  ),
};

// ─── Renderer ────────────────────────────────────────────────────────────────

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  isUser,
}: MarkdownRendererProps) {
  const deferredContent = useDeferredValue(content);

  if (isUser) {
    return <p className="whitespace-pre-wrap">{content}</p>;
  }

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none prose-headings:font-semibold [&>*:first-child]:mt-0">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={markdownComponents}
      >
        {deferredContent}
      </ReactMarkdown>
    </div>
  );
});
