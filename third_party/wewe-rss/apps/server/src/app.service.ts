import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

@Injectable()
export class AppService {
  constructor(private readonly configService: ConfigService) {}

  getHello(): string {
    return `
    <div style="display:flex;justify-content:center;height:100%;align-items:center;font-size:30px;">
      <div>>> <a href="/dash">公众号后台</a> <<</div>
    </div>
    `;
  }
}
