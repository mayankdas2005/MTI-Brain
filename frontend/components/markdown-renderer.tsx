'use client';

import ReactMarkdown, { type Components } from 'react-markdown';
import type { PluggableList } from 'unified';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { ReactNode, useState, memo, useDeferredValue, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import {
  Table, TableHeader, TableBody, TableHead, TableRow, TableCell,
} from '@/components/ui/table';
import { Copy, Check, ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';
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
    <div className="relative bg-muted rounded-lg overflow-hidden mb-2 group">
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
      case 'strong': return <strong key={key}>{ch}</strong>;
      case 'em': return <em key={key}>{ch}</em>;
      case 'del': return <del key={key}>{ch}</del>;
      case 'code': return <code key={key} className="bg-muted px-1.5 py-0.5 rounded text-[0.9em] font-mono">{ch}</code>;
      case 'a': return <a key={key} href={el.properties?.href as string | undefined} className="text-primary hover:underline">{ch}</a>;
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
    <div className="not-prose rounded-xl border border-border overflow-hidden mb-4 [&_tbody_tr:nth-child(even)]:bg-muted/15">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((col, ci) => (
                <TableHead
                  key={ci}
                  className="bg-background whitespace-nowrap cursor-pointer select-none hover:bg-accent transition-colors"
                  onClick={() => handleSort(ci)}
                >
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] uppercase tracking-widest font-medium text-muted-foreground">
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
              <TableRow key={ri}>
                {row.nodes.map((cellNodes, ci) => (
                  <TableCell key={ci} className="text-xs py-2 whitespace-nowrap">
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
  h1: ({ children }) => <h1 className="text-xl font-bold mt-4 mb-2">{children}</h1>,
  h2: ({ children }) => <h2 className="text-lg font-bold mt-3 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-bold mt-2 mb-1">{children}</h3>,
  p: ({ children }) => <p className="mb-2 leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 space-y-1">{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  code: ({ children, className }) => {
    const isBlock = /language-/.test(className || '');
    return isBlock ? (
      <CodeBlock className={className}>{children}</CodeBlock>
    ) : (
      <code className="bg-muted px-1.5 py-0.5 rounded text-[0.9em] font-mono">{children}</code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-primary pl-3 italic text-muted-foreground mb-2">
      {children}
    </blockquote>
  ),
  a: ({ href, children }) => {
    const isExternal = !!href && /^https?:\/\//i.test(href);
    return (
      <a
        href={href}
        {...(isExternal ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
        className="text-primary hover:underline"
      >
        {children}
      </a>
    );
  },
  table: ({ node }) => (
    <SortableMarkdownTable node={node as unknown as HastElement} />
  ),
};

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  isUser,
}: MarkdownRendererProps) {
  const deferredContent = useDeferredValue(content);

  if (isUser) {
    return <p className="whitespace-pre-wrap">{content}</p>;
  }

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
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
