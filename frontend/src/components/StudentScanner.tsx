import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Space } from 'antd'

type Detector = { detect: (source: HTMLVideoElement | ImageBitmap) => Promise<{ rawValue: string }[]> }
type DetectorConstructor = new (options: { formats: string[] }) => Detector

/** 浏览器具备原生二维码识别时启用；无权限/无HTTPS时保留粘贴链接入口。 */
export default function StudentScanner({ onScan }: { onScan: (value: string) => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const stream = useRef<MediaStream | null>(null)
  const timer = useRef<number>()
  const [active, setActive] = useState(false)
  const [error, setError] = useState('')
  const detectorType = (window as unknown as { BarcodeDetector?: DetectorConstructor }).BarcodeDetector
  function stop() {
    window.clearTimeout(timer.current)
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
    setActive(false)
  }
  useEffect(() => () => { window.clearTimeout(timer.current); stream.current?.getTracks().forEach((t) => t.stop()) }, [])

  async function scan() {
    if (!detectorType) return
    setError('')
    try {
      const media = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false })
      if (!video.current) { media.getTracks().forEach((t) => t.stop()); return }
      stream.current = media
      video.current.srcObject = media
      await video.current.play()
      setActive(true)
      const detector = new detectorType({ formats: ['qr_code'] })
      async function tick() {
        if (!stream.current || !video.current) return
        try {
          const found = await detector.detect(video.current)
          if (found[0]) { stop(); onScan(found[0].rawValue); return }
          timer.current = window.setTimeout(() => void tick(), 250)
        } catch { stop(); setError('识别失败，请粘贴教师链接或使用手机相机扫码') }
      }
      void tick()
    } catch { stop(); setError('无法打开相机。请允许相机权限，并使用 HTTPS 或 localhost；也可粘贴链接进入。') }
  }

  async function readImage(file?: File) {
    if (!file || !detectorType) return
    setError('')
    let bitmap: ImageBitmap | undefined
    try {
      bitmap = await createImageBitmap(file)
      const found = await new detectorType({ formats: ['qr_code'] }).detect(bitmap)
      if (found[0]) onScan(found[0].rawValue)
      else setError('图片中未识别到二维码，请使用清晰二维码或粘贴链接')
    } catch { setError('二维码图片识别失败，请粘贴链接') }
    finally { bitmap?.close() }
  }

  return <Space direction="vertical" style={{ width: '100%' }}>
    {detectorType ? <><Button block onClick={() => active ? stop() : void scan()}>{active ? '停止扫码' : '在页面内扫码进入案例'}</Button>
      <label>或识别二维码图片<input aria-label="二维码图片" type="file" accept="image/*" onChange={(e) => void readImage(e.target.files?.[0])} /></label></>
      : <Alert type="info" message="当前浏览器不支持页面内扫码。可用手机相机扫码打开本站案例，或在上方粘贴案例链接。" />}
    <video ref={video} muted playsInline style={{ width: '100%', display: active ? 'block' : 'none' }} />
    {error && <Alert type="warning" message={error} />}
  </Space>
}
