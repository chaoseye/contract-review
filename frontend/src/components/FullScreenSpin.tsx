import { Flex, Spin } from 'antd'

export function FullScreenSpin() {
  return (
    <Flex justify="center" align="center" style={{ minHeight: '100vh' }}>
      <Spin size="large" />
    </Flex>
  )
}
