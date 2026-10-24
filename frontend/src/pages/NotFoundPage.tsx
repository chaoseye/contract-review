import { Button, Result } from 'antd'
import { Link } from 'react-router'

export function NotFoundPage() {
  return (
    <Result
      status="404"
      title="Страница не найдена"
      extra={
        <Link to="/">
          <Button type="primary">На главную</Button>
        </Link>
      }
    />
  )
}
