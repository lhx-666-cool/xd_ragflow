import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Images } from '@/constants/common';
import { cn } from '@/lib/utils';
import DocxViewer from '@/pages/document-viewer/docx';
import ExcelViewer from '@/pages/document-viewer/excel';
import DocumentImage from '@/pages/document-viewer/image';
import MdViewer from '@/pages/document-viewer/md';
import PdfPreviewer from '@/pages/document-viewer/pdf';
import TextViewer from '@/pages/document-viewer/text';
import { api_host } from '@/utils/api';
import { previewHtmlFile } from '@/utils/file-util';
import { ArrowLeft, MessageSquare, Send } from 'lucide-react';
import type { KeyboardEventHandler } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'umi';
import styles from './index.less';

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
};

const buildDocumentUrl = (prefix: string, documentId?: string) => {
  if (!documentId) return '';
  return `${api_host}/${prefix}/get/${documentId}`;
};

const DocumentChatPage = () => {
  const navigate = useNavigate();
  const { id: documentId } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'assistant-initial',
      role: 'assistant',
      content:
        '样式已准备就绪，稍后接入后端 API 后即可在此向文档提问并查看返回结果。',
    },
  ]);
  const messageListRef = useRef<HTMLDivElement>(null);
  const htmlPreviewedRef = useRef(false);

  const prefix = searchParams.get('prefix') || 'document';
  const extension = useMemo(() => {
    if (searchParams.get('ext')) {
      return searchParams.get('ext')!.toLowerCase();
    }
    const name = searchParams.get('name');
    if (name?.includes('.')) {
      return name.split('.').pop()!.toLowerCase();
    }
    return 'pdf';
  }, [searchParams]);
  const documentName = searchParams.get('name') || '文档';

  const documentUrl = useMemo(
    () => buildDocumentUrl(prefix, documentId),
    [prefix, documentId],
  );

  const openOriginalPreview = useCallback(() => {
    if (!documentId) return;
    window.open(
      `/document/${documentId}?ext=${extension}&prefix=${prefix}`,
      '_blank',
    );
  }, [documentId, extension, prefix]);

  const renderFallback = useCallback(
    (message?: string) => (
      <div className={styles.fallbackWrapper}>
        <p>
          {message ||
            `暂不支持 ${(extension || '该').toUpperCase()} 文件在此预览。`}
        </p>
        <Button onClick={openOriginalPreview}>打开原始预览</Button>
      </div>
    ),
    [extension, openOriginalPreview],
  );

  const renderDocumentContent = useCallback(() => {
    if (!documentUrl) {
      return renderFallback('未找到文档内容。');
    }

    if (extension === 'html' && documentId) {
      if (!htmlPreviewedRef.current) {
        previewHtmlFile(documentId);
        htmlPreviewedRef.current = true;
      }
      return renderFallback('HTML 文件将在新窗口中打开。');
    }

    if (extension === 'pdf') {
      return (
        <div className={styles.documentContainer}>
          <PdfPreviewer url={documentUrl} />
        </div>
      );
    }

    if (extension === 'md') {
      return <MdViewer filePath={documentUrl} />;
    }

    if (extension === 'txt') {
      return <TextViewer filePath={documentUrl} />;
    }

    if (extension === 'docx') {
      return <DocxViewer filePath={documentUrl} />;
    }

    if (extension === 'xlsx' || extension === 'xls') {
      return <ExcelViewer filePath={documentUrl} />;
    }

    if (Images.includes(extension)) {
      return (
        <div className={styles.imageWrapper}>
          <DocumentImage src={documentUrl} preview={false} />
        </div>
      );
    }

    return renderFallback();
  }, [
    documentUrl,
    renderFallback,
    extension,
    documentId,
    htmlPreviewedRef,
  ]);

  const handleBack = () => {
    navigate(-1);
  };

  const handleSend = () => {
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    const timestamp = Date.now();
    setMessages((prev) => [
      ...prev,
      {
        id: `user-${timestamp}`,
        role: 'user',
        content: trimmed,
      },
      {
        id: `assistant-${timestamp + 1}`,
        role: 'assistant',
        content: '这里将展示模型回复，目前仅展示样式。',
      },
    ]);
    setInputValue('');
  };

  useEffect(() => {
    if (messageListRef.current) {
      messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
    }
  }, [messages]);

  const handleTextareaKeyDown: KeyboardEventHandler<HTMLTextAreaElement> = (
    event,
  ) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      handleSend();
    }
  };

  if (!documentId) {
    return (
      <section className="flex h-screen items-center justify-center bg-bg-base text-text-primary">
        <div className="text-center space-y-3">
          <p>未找到文档信息。</p>
          <Button variant="secondary" onClick={() => navigate(-1)}>
            返回
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section className="flex flex-col h-screen bg-bg-base text-text-primary">
      <header className="flex items-center gap-3 px-6 h-16 border-b border-border-default bg-bg-card">
        <Button variant="ghost" size="icon" onClick={handleBack}>
          <ArrowLeft className="size-5" />
        </Button>
        <div className="flex flex-col overflow-hidden">
          <span className="font-semibold truncate">{documentName}</span>
          <span className="text-xs text-text-secondary">文档预览 · 对话模式</span>
        </div>
      </header>
      <div className="flex flex-1 min-h-0 flex-col lg:flex-row">
        <div
          className={cn(
            'flex-1 lg:w-1/2 min-h-[40vh] lg:min-h-0 border-b lg:border-b-0 lg:border-r border-border-default overflow-hidden flex flex-col',
            styles.viewerPane,
          )}
        >
          <div className={styles.viewerBody}>{renderDocumentContent()}</div>
        </div>
        <div className="flex-1 lg:w-1/2 min-w-[320px] border-border-default bg-bg-base flex flex-col border-t lg:border-t-0 lg:border-l">
          <div className="px-5 py-4 border-b border-border-default">
            <div className="flex items-center gap-2 font-semibold text-sm">
              <MessageSquare className="size-4" />
              文档聊天
            </div>
            <p className="text-xs text-text-secondary mt-1">
              当前仅展示前端样式，发送后会模拟一条回复。
            </p>
          </div>
          <div
            ref={messageListRef}
            className="flex-1 overflow-auto px-4 py-5 space-y-3"
          >
            {messages.map((message) => (
              <div
                key={message.id}
                className={cn('flex w-full', {
                  'justify-end': message.role === 'user',
                })}
              >
                <div
                  className={cn(
                    'px-4 py-2 rounded-2xl max-w-[85%] text-sm leading-relaxed bg-bg-card text-text-primary',
                    {
                      'bg-primary text-primary-foreground':
                        message.role === 'user',
                    },
                  )}
                >
                  {message.content}
                </div>
              </div>
            ))}
          </div>
          <div className="border-t border-border-default p-4 space-y-3">
            <Textarea
              rows={3}
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              onKeyDown={handleTextareaKeyDown}
              placeholder="向文档提问，Ctrl / ⌘ + Enter 发送"
            />
            <Button
              className="w-full"
              onClick={handleSend}
              disabled={!inputValue.trim()}
            >
              <Send className="size-4" />
              发送
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
};

export default DocumentChatPage;
