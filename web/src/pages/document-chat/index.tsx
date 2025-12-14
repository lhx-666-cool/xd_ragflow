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
import { RAGFlowSelect } from '@/components/ui/select';
import kbService, { createDocumentSignedUrl } from '@/services/knowledge-service';
import { api_host } from '@/utils/api';
import { previewHtmlFile } from '@/utils/file-util';
import {
  ArrowLeft,
  MessageSquare,
  Send,
} from 'lucide-react';
import type { KeyboardEventHandler } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'umi';
import styles from './index.less';

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
};

const EraserIcon = (props: React.SVGProps<SVGSVGElement>) => (
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    {...props}
  >
    <path d="m17 3 4 4L10 18H6L3 15l11-11Z" />
    <path d="m6 18 2.5-2.5" />
    <path d="M14 4l6 6" />
    <path d="M2 21h14" />
  </svg>
);

const DOCUMENT_CHAT_CACHE_PREFIX = 'documentChatHistory:';
const DEFAULT_ASSISTANT_MESSAGE: ChatMessage = {
  id: 'assistant-initial',
  role: 'assistant',
  content: '样式已准备就绪，稍后接入后端 API 后即可在此向文档提问并查看返回结果。',
};

const getChatCacheKey = (documentId?: string) =>
  documentId ? `${DOCUMENT_CHAT_CACHE_PREFIX}${documentId}` : '';

const getCachedMessages = (documentId?: string): ChatMessage[] | null => {
  if (!documentId || typeof window === 'undefined' || !window.localStorage) {
    return null;
  }
  try {
    const raw = window.localStorage.getItem(getChatCacheKey(documentId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return parsed;
    }
  } catch (error) {}
  return null;
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
  const [activeTab, setActiveTab] = useState<'chat' | 'summary' | 'translate'>(
    'chat',
  );
  const [modelName, setModelName] = useState('xdechat');
  const [retrievalChunks, setRetrievalChunks] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    const cached = getCachedMessages(documentId);
    return cached ?? [DEFAULT_ASSISTANT_MESSAGE];
  });
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

  const handleSend = async () => {
    const trimmed = inputValue.trim();
    if (!trimmed || sending || !documentId) return;
    const timestamp = Date.now();
    const assistantId = `assistant-${timestamp + 1}`;
    setMessages((prev) => [
      ...prev,
      {
        id: `user-${timestamp}`,
        role: 'user',
        content: trimmed,
      },
      {
        id: assistantId,
        role: 'assistant',
        content: '正在生成...',
      },
    ]);
    setInputValue('');
    setSending(true);

    try {
      // 调用后端接口获取带签名的文档URL
      const signedUrlResponse = await createDocumentSignedUrl(documentId, 30);
      const docUrl = signedUrlResponse?.data?.data?.signed_url || '';

      const requestBody = {
        model_name: modelName,
        user_input: [{ role: 'user', content: trimmed }],
        retrieval_chunks: retrievalChunks,
        doc_url: docUrl,
      };

      const response = await fetch(
        'https://huitong.xidian.edu.cn/ragflow_hooks/parallel_sample',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(requestBody),
        },
      );
      const result = await response.json();
      const extractResult = result?.extract_result || '暂无结果';
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? { ...message, content: extractResult }
            : message,
        ),
      );
    } catch (error) {
      console.error('Failed to request parallel sample', error);
      setMessages((prev) =>
        prev.map((message) =>
          message.id === assistantId
            ? { ...message, content: '请求失败，请稍后重试。' }
            : message,
        ),
      );
    } finally {
      setSending(false);
    }
  };

  const handleClearContext = () => {
    setMessages([DEFAULT_ASSISTANT_MESSAGE]);
    if (documentId && typeof window !== 'undefined' && window.localStorage) {
      try {
        window.localStorage.removeItem(getChatCacheKey(documentId));
      } catch (error) {}
    }
  };

  useEffect(() => {
    if (messageListRef.current) {
      messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    const cached = getCachedMessages(documentId);
    if (cached) {
      setMessages(cached);
    } else {
      setMessages([DEFAULT_ASSISTANT_MESSAGE]);
    }
  }, [documentId]);

  useEffect(() => {
    if (!documentId || typeof window === 'undefined' || !window.localStorage) {
      return;
    }
    try {
      window.localStorage.setItem(
        getChatCacheKey(documentId),
        JSON.stringify(messages),
      );
    } catch (error) {}
  }, [documentId, messages]);

  useEffect(() => {
    const fetchChunkList = async () => {
      if (!documentId) return;
      try {
        const { data } = await kbService.chunk_list({
          doc_id: documentId,
          keywords: '',
          page: 1,
          size: 30,
        });
        const chunks = data?.data?.chunks ?? [];
        const contentList = chunks.map(
          (item: { content_with_weight?: string }) =>
            item?.content_with_weight || '',
        );
        setRetrievalChunks(contentList);
        console.log('document', documentName, 'content_with_weight', contentList);
      } catch (error) {
        console.error('Failed to fetch chunk list', error);
      }
    };

    fetchChunkList();
  }, [documentId, documentName]);

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
          <div className={styles.viewerBody}>
            <div className={styles.viewerContent}>{renderDocumentContent()}</div>
          </div>
        </div>
        <div className="flex-1 lg:w-1/2 min-w-[320px] border-border-default bg-bg-base flex flex-col border-t lg:border-t-0 lg:border-l">
          <div className="px-5 py-3 border-b border-border-default">
            <div className="grid grid-cols-3 gap-2 text-sm font-semibold">
              <button
                type="button"
                className={cn(
                  'flex items-center justify-center gap-1 px-2 py-2 rounded-md transition-colors w-full',
                  activeTab === 'chat'
                    ? 'bg-primary/10 text-primary'
                    : 'text-text-secondary hover:text-text-primary',
                )}
                onClick={() => setActiveTab('chat')}
              >
                <MessageSquare className="size-4" />
                <span>文档聊天</span>
              </button>
              <button
                type="button"
                className={cn(
                  'flex items-center justify-center px-2 py-2 rounded-md transition-colors w-full',
                  activeTab === 'summary'
                    ? 'bg-primary/10 text-primary'
                    : 'text-text-secondary hover:text-text-primary',
                )}
                onClick={() => setActiveTab('summary')}
              >
                文档总结
              </button>
              <button
                type="button"
                className={cn(
                  'flex items-center justify-center px-2 py-2 rounded-md transition-colors w-full',
                  activeTab === 'translate'
                    ? 'bg-primary/10 text-primary'
                    : 'text-text-secondary hover:text-text-primary',
                )}
                onClick={() => setActiveTab('translate')}
              >
                文档翻译
              </button>
            </div>
            <p className="text-xs text-text-secondary mt-1">
              发送后将调用并行采样接口并返回提取结果。
            </p>
            <div className="flex items-center gap-3 mt-2 text-sm">
              <span className="text-text-secondary">模型</span>
              <div className="w-40">
                <RAGFlowSelect
                  value={modelName}
                  onChange={(val) => setModelName(val || 'xdechat')}
                  options={[{ label: 'xdechat', value: 'xdechat' }]}
                  placeholder="选择模型"
                />
              </div>
              <Button
                size="sm"
                variant="outline"
                title="清空上下文"
                className="w-9 h-9 p-0 text-text-secondary border-border-default hover:border-primary/50 hover:text-primary hover:bg-primary/10 rounded-md"
                onClick={handleClearContext}
              >
                <EraserIcon className="size-4" />
              </Button>
            </div>
          </div>
          {activeTab === 'chat' ? (
            <>
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
                  disabled={!inputValue.trim() || sending}
                >
                  <Send className="size-4" />
                  发送
                </Button>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-text-disabled text-sm">
              {/* 留空占位 */}
            </div>
          )}
        </div>
      </div>
    </section>
  );
};

export default DocumentChatPage;
