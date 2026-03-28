import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

@Injectable()
export class AppService {
  constructor(private readonly configService: ConfigService) {}

  getHello(): string {
    return `
    <div style="display:flex;justify-content:center;height:100%;align-items:center;font-size:24px;font-family:'Noto Sans SC','Microsoft YaHei',sans-serif;">
      <div><a href="/dash" style="color:inherit;text-decoration:none;">进入公众号后台</a></div>
    </div>
    `;
  }
}
