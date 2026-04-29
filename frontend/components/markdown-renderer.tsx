'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { ReactNode, useState, isValidElement, Children } from 'react';
import { Button } from '@/components/ui/button';
import { Copy, Check } from 'lucide-react';
import { copyText } from '@/lib/utils';

interface MarkdownRendererProps {
  content: string;
  isUser?: boolean;
}

interface CodeBlockProps {
  children: ReactNode;
  className?: string;
}

function CodeBlock({ children, className }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const code = String(children).replace(/\n$/, '');

  const copyCode = async () => {
    const ok = await copyText(code);
    if (!ok) return;
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
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity"
        onClick={copyCode}
      >
        {copied ? (
          <Check className="w-4 h-4" />
        ) : (
          <Copy className="w-4 h-4" />
        )}
      </Button>
    </div>
  );
}

export function MarkdownRenderer({ content, isUser }: MarkdownRendererProps) {
  if (isUser) {
    return <p className="whitespace-pre-wrap">{content}</p>;
  }

  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-xl font-bold mt-4 mb-2">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-lg font-bold mt-3 mb-2">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-base font-bold mt-2 mb-1">{children}</h3>
          ),
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
          ul: ({ children }) => (
            <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-5 mb-2 space-y-1">
              {children}
            </ol>
          ),
          li: ({ children }) => (
            <li className="text-sm pl-1">{children}</li>
          ),
          code: ({ children, className }) => {
            const isBlock = /language-/.test(className || '');
            return isBlock ? (
              <CodeBlock className={className}>
                {children}
              </CodeBlock>
            ) : (
              <code className="bg-muted px-1.5 py-0.5 rounded text-sm font-mono">
                {children}
              </code>
            );
          },
          pre: ({ children }) => <>{children}</>,
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-primary pl-3 italic text-muted-foreground mb-2">
              {children}
            </blockquote>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              {children}
            </a>
          ),
          table: ({ children }) => (
            <table className="border-collapse border border-border w-full mb-2">
              {children}
            </table>
          ),
          th: ({ children }) => (
            <th className="border border-border p-2 bg-muted text-left font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-border p-2">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
