export const downloadFile = ({
  data,
  fileName,
  fileType,
}: {
  data: string | ArrayBuffer;
  fileName: string;
  fileType: string;
}) => {
  const blob = new Blob([data], { type: fileType });
  const aElem = document.createElement('a');
  aElem.download = fileName;
  aElem.href = window.URL.createObjectURL(blob);
  const clickEvt = new MouseEvent('click', {
    view: window,
    bubbles: true,
    cancelable: true,
  });
  aElem.dispatchEvent(clickEvt);
  aElem.remove();
};
