'use client';

import ReactMarkdown, { type Components } from 'react-markdown';
import type { PluggableList } from 'unified';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import { ReactNode, useState, isValidElement, Children, memo, useDeferredValue } from 'react';
import { Button } from '@/components/ui/button';
import { Copy, Check } from 'lucide-react';
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

const markdownComponents: Components = {
  h1: ({ children }) => <h1 className="text-xl font-bold mt-4 mb-2">{children}</h1>,
  h2: ({ children }) => <h2 className="text-lg font-bold mt-3 mb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-base font-bold mt-2 mb-1">{children}</h3>,
  p: ({ children }) => {
    const hasBlock = Children.toArray(children).some(
      (child) => isValidElement(child) && typeof child.type !== 'string'
    );
    return hasBlock ? (
      <div className="mb-2 leading-relaxed">{children}</div>
    ) : (
      <p className="mb-2 leading-relaxed">{children}</p>
    );
  },
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
  table: ({ children }) => (
    <table className="border-collapse border border-border w-full mb-2">{children}</table>
  ),
  th: ({ children }) => (
    <th className="border border-border p-2 bg-muted text-left font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border border-border p-2">{children}</td>,
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
