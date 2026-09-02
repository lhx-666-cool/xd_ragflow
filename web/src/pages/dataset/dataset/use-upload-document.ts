import { UploadFormSchemaType } from '@/components/file-upload-dialog';
import message from '@/components/ui/message';
import { useSetModalState } from '@/hooks/common-hooks';
import {
  useRunDocument,
  useSubmitTextbookKg,
  useUploadNextDocument,
} from '@/hooks/use-document-request';
import { getUnSupportedFilesCount } from '@/utils/document-util';
import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

export const useHandleUploadDocument = () => {
  const { t } = useTranslation();
  const {
    visible: documentUploadVisible,
    hideModal: hideDocumentUploadModal,
    showModal: showDocumentUploadModal,
  } = useSetModalState();
  const { uploadDocument, loading } = useUploadNextDocument();
  const { submitTextbookKg, loading: textbookKgLoading } =
    useSubmitTextbookKg();
  const { runDocumentByIds } = useRunDocument();

  const onDocumentUploadOk = useCallback(
    async ({
      fileList,
      parseOnCreation,
      buildTextbookKg,
    }: UploadFormSchemaType) => {
      if (fileList.length > 0) {
        const ret = await uploadDocument(fileList);
        if (typeof ret?.message !== 'string') {
          return;
        }

        if (ret.code === 0 && parseOnCreation) {
          runDocumentByIds({
            documentIds: ret.data.map((x) => x.id),
            run: 1,
            shouldDelete: false,
          });
        }

        if (ret.code === 0 && buildTextbookKg) {
          const pdfDocumentIds = ret.data
            .filter(
              (document) =>
                document.suffix?.toLowerCase().replace(/^\./, '') === 'pdf' ||
                document.name?.toLowerCase().endsWith('.pdf'),
            )
            .map((document) => document.id);
          if (pdfDocumentIds.length === 0) {
            message.warning(t('fileManager.textbookKgOnlyPdf'));
          } else {
            const textbookResult = await submitTextbookKg(pdfDocumentIds);
            if (textbookResult?.code !== 0) {
              message.warning(t('fileManager.textbookKgSubmitFailed'));
            } else if (textbookResult?.data?.failed?.length) {
              message.warning(t('fileManager.textbookKgPartialFailure'));
            } else {
              message.success(t('fileManager.textbookKgSubmitted'));
            }
          }
        }

        const count = getUnSupportedFilesCount(ret?.message);
        /// 500 error code indicates that some file types are not supported
        let code = ret?.code;
        if (
          ret?.code === 0 ||
          (ret?.code === 500 && count !== fileList.length) // Some files were not uploaded successfully, but some were uploaded successfully.
        ) {
          code = 0;
          hideDocumentUploadModal();
        }
        return code;
      }
    },
    [
      uploadDocument,
      runDocumentByIds,
      submitTextbookKg,
      hideDocumentUploadModal,
      t,
    ],
  );

  return {
    documentUploadLoading: loading || textbookKgLoading,
    onDocumentUploadOk,
    documentUploadVisible,
    hideDocumentUploadModal,
    showDocumentUploadModal,
  };
};
